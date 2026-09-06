_ns = {}

def bind(module_dict):
    global _ns
    _ns = module_dict

def get(name, default=None):
    return _ns.get(name, default)

def set_val(name, value):
    _ns[name] = value
