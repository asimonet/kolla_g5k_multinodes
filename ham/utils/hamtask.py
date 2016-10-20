# -*- coding: utf-8 -*-
from provider.g5k import G5K
from utils.constants import SYMLINK_NAME
from functools import wraps

import os
import yaml
import logging

def load_env():
    env = {
        'config' : {},      # The config
        'resultdir': '',    # Path to the result directory
        'config_file' : '', # The initial config file
        'nodes'  : {},      # Roles with nodes
        'phase'  : '',      # Last phase that have been run
        'user'   : '',      # User id for this job
        'kolla_repo': 'https://git.openstack.org/openstack/kolla',
        'kolla_branch': 'stable/mitaka'
    }

    # Loads the previously saved environment (if any)
    env_path = os.path.join(SYMLINK_NAME, 'env')
    if os.path.isfile(env_path):
        with open(env_path, 'r') as f:
            env.update(yaml.load(f))
            logging.info("Reloaded config %s", env['config'])

    # Resets the configuration of the environment
    if os.path.isfile(env['config_file']):
        with open(env['config_file'], 'r') as f:
            env['config'].update(yaml.load(f))
            logging.info("Reloaded config %s", env['config'])

    return env

def save_env(env):
    env_path = os.path.join(env['resultdir'], 'env')

    if os.path.isdir(env['resultdir']):
        with open(env_path, 'w') as f:
            yaml.dump(env, f)

def hamtask(doc):
    """Decorator for a Ham Task."""
    def decorator(fn):
        fn.__doc__ = doc
        @wraps(fn)
        def decorated(*args, **kwargs):
            logging.info("Ham kwargs %s", kwargs)

            # TODO: Dynamically loads the provider
            if kwargs.has_key('--provider'):
                provider_name = kwargs['--provider']
                kwargs['provider'] = G5K()

            # Loads the environment & set the config
            env = load_env()
            kwargs['env'] = env

            # Proceeds with the function executio
            fn(*args, **kwargs)

            # Save the environment
            save_env(env)
        return decorated
    return decorator
