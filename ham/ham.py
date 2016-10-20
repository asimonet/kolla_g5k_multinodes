# -*- coding: utf-8 -*-
"""Ham: Monitor and test your OpenStack.

usage: ham [-h|--help] [-v|-s|--silent] <command> [<args> ...]

Options:
  -h --help      Show this help message.
  -v             Verbose mode.
  -s --silent    Quiet mode.

Commands:
  up             Get resources and install the docker registry.
  os             Run kolla and install OpenStack.
  init           TODO: explain me.
  bench          Run rally on this OpenStack.
  ssh-tunnel     Print configuration for port forwarding with horizon.
  info           Show information of the actual deployment.

See 'ham <command> --help' for more information on a specific command.
"""
from utils.constants import *
from utils.extra import *
from utils.hamtask import hamtask

from datetime import datetime
import logging

from docopt import docopt
import requests
import pprint
from operator import itemgetter, attrgetter

from keystoneauth1.identity import v3
from keystoneauth1 import session
from glanceclient import client as gclient
from keystoneclient.v3 import client as kclient

import os
import sys
from subprocess import call

import yaml

CALL_PATH = os.getcwd()

@hamtask("""
usage: ham up [-f CONFIG_PATH] [--force-deploy] [-t TAGS | --tags=TAGS]
              [--provider=PROVIDER] [-v|-s|--silent]

  -h --help            Show this help message.
  -f CONFIG_PATH       Path to the configuration file describing the
                       deployment [default: ./reservation.yaml].
  --force-deploy       Force deployment [default: False].
  -t TAGS --tags=TAGS  Only run ansible tasks tagged with these values.
  --provider=PROVIDER  The provider name [default: G5K].

""")
def up(provider=None, env=None, **kwargs):
    logging.info('phase[up]')

    # Loads the configuration file
    config_file = kwargs['-f']
    if os.path.isfile(config_file):
        env['config_file'] = config_file
        with open(config_file, 'r') as f:
            env['config'].update(yaml.load(f))
            logging.info("Reloaded config %s", env['config'])
    else:
        logging.error('Configuration file %s does not exist', config_file)

    # Calls the provider and initialise resources
    rsc,ips,eths = provider.init(env['config'], kwargs['--force-deploy'])

    env['rsc'] = rsc
    env['ips'] = ips
    env['eths'] = eths

    # Generates a directory for results
    resultdir_name = 'ham_' + datetime.today().isoformat()
    resultdir = os.path.join(CALL_PATH, resultdir_name)
    os.mkdir(resultdir)
    logging.info('Generates result directory %s' % resultdir_name)

    env['resultdir'] = resultdir

    # Generates inventory for ansible/kolla
    base_inventory = env['config']['inventory']
    inventory = os.path.join(resultdir, 'multinode')
    generate_inventory(env['rsc'], base_inventory, inventory)
    logging.info('Generates inventory %s' % inventory)

    env['inventory'] = inventory

    # Set variables required by playbooks of the application
    env['config'].update({
        # Ham specific
        'vip':          ips[INTERNAL_IP],
        'registry_vip': ips[REGISTRY_IP],
        'influx_vip':   ips[INFLUX_IP],
        'grafana_vip':  ips[GRAFANA_IP],
        # Kolla + common specific
        'neutron_external_address': ips[NEUTRON_IP],
        'network_interface': eths[NETWORK_IFACE],
        # Kolla specific
        'kolla_internal_vip_address': ips[INTERNAL_IP],
        'neutron_external_interface': eths[EXTERNAL_IFACE]
    })
    passwords = os.path.join(TEMPLATE_DIR, "passwords.yml")
    with open(passwords) as f:
        env['config'].update(yaml.load(f))

    # Executes hooks and runs playbook that initializes resources (eg,
    # installs the registry, install monitoring tools, ...)
    provider.before_preintsall(env)
    up_playbook = os.path.join(ANSIBLE_DIR, 'up.yml')
    run_ansible([up_playbook], inventory, env['config'], kwargs['--tags'])
    provider.after_preintsall(env)

    # Symlink current directory
    link = os.path.abspath(SYMLINK_NAME)
    try:
        os.remove(link)
    except OSError:
        pass
    os.symlink(resultdir, link)
    logging.info("Symlinked %s to %s" % (resultdir, link))


@hamtask("""
usage: ham os [--reconfigure] [-t TAGS | --tags=TAGS] [-v|-s|--silent]

  -h --help            Show this help message.
  -t TAGS --tags=TAGS  Only run ansible tasks tagged with these values.
  --reconfigure        Reconfigure the services after a deployment.
""")
def install_os(env=None, **kwargs):
    # Generates kolla globals.yml, passwords.yml
    generated_kolla_vars = {
        # Kolla + common specific
        'neutron_external_address'   : env['ips'][NEUTRON_IP],
        'network_interface'          : env['eths'][NETWORK_IFACE],
        # Kolla specific
        'kolla_internal_vip_address' : env['ips'][INTERNAL_IP],
        'neutron_external_interface' : env['eths'][EXTERNAL_IFACE]
    }
    generate_kolla_files(env['config']["kolla"], generated_kolla_vars, env['resultdir'])

    # Clone or pull Kolla
    kolla_path = os.path.join(env['resultdir'], 'kolla')
    if os.path.isdir(kolla_path):
        logging.info("Remove previous Kolla installation")
        call("rm -rf %s" % kolla_path, shell=True)

    logging.info("Cloning Kolla")
    call("git clone %s -b %s %s > /dev/null" % (env['kolla_repo'], env['kolla_branch'], kolla_path), shell=True)

    logging.warning(("Patching kolla, this should be ",
                     "deprecated with the new version of Kolla"))

    playbook = os.path.join(ANSIBLE_DIR, "patches.yml")
    run_ansible([playbook], env['inventory'], env['config'])

    kolla_cmd = [os.path.join(kolla_path, "tools", "kolla-ansible")]

    if kwargs['--reconfigure']:
        kolla_cmd.append('reconfigure')
    else:
        kolla_cmd.append('deploy')

    kolla_cmd.extend(["-i", "%s/multinode" % SYMLINK_NAME,
                      "--configdir", "%s" % SYMLINK_NAME])

    if kwargs['--tags']:
        kolla_cmd.extend(['--tags', kwargs['--tags']])

    call(kolla_cmd)


@hamtask("""
usage: ham init [-v|-s|--silent]

  -h --help            Show this help message.
""")
def init_os(env=None, **kwargs):
    # Authenticate to keystone
    # http://docs.openstack.org/developer/keystoneauth/using-sessions.html
    # http://docs.openstack.org/developer/python-glanceclient/apiv2.html
    keystone_addr = env['config']['vip']

    auth = v3.Password(auth_url='http://%s:5000/v3' % keystone_addr,
                       username='admin',
                       password='demo',
                       project_name='admin',
                       user_domain_id='Default',
                       project_domain_id='default')
    sess = session.Session(auth=auth)

    # Install `member` role
    keystone = kclient.Client(session=sess)
    role_name = 'member'
    if role_name not in map(attrgetter('name'), keystone.roles.list()):
        logging.info("Creating role %s" % role_name)
        keystone.roles.create(role_name)

    # Install cirros with glance client if absent
    glance = gclient.Client('2', session=sess)
    cirros_name = 'cirros.uec'
    if cirros_name not in map(itemgetter('name'), glance.images.list()):
        # Download cirros
        image_url  = 'http://download.cirros-cloud.net/0.3.4/'
        image_name = 'cirros-0.3.4-x86_64-disk.img'
        logging.info("Downloading %s at %s..." % (cirros_name, image_url))
        cirros_img = requests.get(image_url + '/' + image_name)

        # Install cirros
        cirros = glance.images.create(name=cirros_name,
                                      container_format='bare',
                                      disk_format='qcow2',
                                      visibility='public')
        glance.images.upload(cirros.id, cirros_img.content)
        logging.info("%s has been created on OpenStack" %  cirros_name)

@hamtask("""
usage: ham bench [--scenarios=SCENARIOS] [--times=TIMES]
                 [--concurrency=CONCURRENCY] [--wait=WAIT]
                 [-v|-s|--silent]

  -h --help                 Show this help message.
  --scenarios=SCENARIOS     Name of the files containing the scenarios
                            to launch. The file must reside under the
                            rally directory.
  --times=TIMES             Number of times to run each scenario
                            [default: 1].
  --concurrency=CONCURRENCY Concurrency level of the tasks in each
                            scenario [default: 1].
  --wait=WAIT               Seconds to wait between two scenarios
                            [default: 0].
""")
def bench(env=None, **kwargs):
    playbook_path = os.path.join(ANSIBLE_DIR, 'run-bench.yml')
    inventory_path = os.path.join(SYMLINK_NAME, 'multinode')
    if kwargs["--scenarios"]:
        env['config']['rally_scenarios_list'] = kwargs["--scenarios"]
    env['config']['rally_times'] = kwargs["--times"]
    env['config']['rally_concurrency'] = kwargs["--concurrency"]
    env['config']['rally_wait'] = kwargs["--wait"]
    run_ansible([playbook_path], inventory_path, env['config'])

@hamtask("""usage: ham ssh-tunnel""")
def ssh_tunnel(env=None, **kwargs):
    user = env['user']
    internal_vip_address = env['config']['vip']

    logging.info("ssh tunnel informations:")
    logging.info("___")

    script = "cat > /tmp/openstack_ssh_config <<EOF\n"
    script += "Host *.grid5000.fr\n"
    script += "  User " + user + " \n"
    script += "  ProxyCommand ssh -q " + user + "@194.254.60.4 nc -w1 %h %p # Access South\n"
    script += "EOF\n"

    port = 8080
    script += "ssh -F /tmp/openstack_ssh_config -N -L " + \
              str(port) + ":" + internal_vip_address + ":80 " + \
              user + "@access.grid5000.fr &\n"

    script += "echo 'http://localhost:8080'\n"

    logging.info(script)
    logging.info("___")

@hamtask("usage: ham info")
def info(env=None, **kwargs):
    pprint.pprint(env)


if __name__ == "__main__":
    args = docopt(__doc__,
                  version='ham version 0.1',
                  options_first=True)

    if '-v' in args['<args>']:
        logging.basicConfig(level = logging.DEBUG)
    elif '-s' in args['<args>'] or '--silent' in args['<args>']:
        logging.basicConfig(level = logging.ERROR)
    else:
        logging.basicConfig(level = logging.INFO)

    argv = [args['<command>']] + args['<args>']

    if args['<command>'] == 'up':
        up(**docopt(up.__doc__, argv=argv))
    elif args['<command>'] == 'os':
        install_os(**docopt(install_os.__doc__, argv=argv))
    elif args['<command>'] == 'init':
        init_os(**docopt(init_os.__doc__, argv=argv))
    elif args['<command>'] == 'bench':
        bench(**docopt(bench.__doc__, argv=argv))
    elif args['<command>'] == 'ssh-tunnel':
        ssh_tunnel(**docopt(ssh_tunnel.__doc__, argv=argv))
    elif args['<command>'] == 'info':
        info(**docopt(info.__doc__, argv=argv))
    else: pass

