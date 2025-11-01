#!/usr/bin/python3

import re
import sys

def read_config_file(file_path):
    """Reads the configuration file and returns a dictionary of key-value pairs."""
    config_dict = {}
    with open(file_path, 'r') as file:
        for line in file:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue

            # Match the CONFIG_KEY=value format
            match = re.match(r'^(CONFIG_[A-Za-z0-9_]+)=(.*)$', line)
            if match:
                key, value = match.groups()
                config_dict[key] = value
            # Handle the case for "# CONFIG_KEY is not set"
            elif re.match(r'^# CONFIG_[A-Za-z0-9_]+ is not set$', line):
                key = line.split(' ')[1]
                config_dict[key] = None  # This indicates the entry is unset
    return config_dict


def validate_configs(config_file, fragment_file):
    """Compares the config file with a config fragment and reports matching, mismatching, and missing entries."""

    # Read the config and fragment files
    config_dict = read_config_file(config_file)
    fragment_dict = read_config_file(fragment_file)

    # Report results
    matching = []
    mismatching = []
    missing = []

    for key, fragment_value in fragment_dict.items():
        # Check for missing or mismatching entries
        if key not in config_dict:
            missing.append(key)
        elif config_dict[key] != fragment_value:
            mismatching.append((key, config_dict[key], fragment_value))
        else:
            matching.append(key)

    # Output the results
    print("Matching entries:")
    for entry in matching:
        print(f"  {entry}")

    print("\nMismatching entries:")
    for entry, actual, expected in mismatching:
        print(f"  {entry}: expected '{expected}', got '{actual}'")

    print("\nMissing entries:")
    for entry in missing:
        print(f"  {entry} is missing")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python validate_kernel_config.py <.config> <config_fragment>")
        sys.exit(1)

    config_file = sys.argv[1]
    fragment_file = sys.argv[2]

    validate_configs(config_file, fragment_file)
