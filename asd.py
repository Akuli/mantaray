import platformdirs, appdirs
print("appdirs:", appdirs.user_config_dir("mantaray", "Akuli"))
print("platformdirs:", platformdirs.user_config_dir("mantaray", "Akuli"))
assert appdirs.user_config_dir("mantaray", "Akuli") == platformdirs.user_config_dir("mantaray", "Akuli")
