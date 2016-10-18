import os

# PATH constants
HAM_PATH = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SYMLINK_NAME = os.path.join(HAM_PATH, '..', 'current')
TEMPLATE_DIR = os.path.join(HAM_PATH, 'templates')
ANSIBLE_DIR = os.path.join(HAM_PATH, 'ansible')

# IP constants
INTERNAL_IP = 0
REGISTRY_IP = 1
INFLUX_IP   = 2
GRAFANA_IP  = 3
NEUTRON_IP  = 4

# NIC constants
NETWORK_IFACE  = 0
EXTERNAL_IFACE = 1
