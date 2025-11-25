#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from argparse import ArgumentParser
from enum import Enum
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from typing import Dict, List

KERNEL_BUILD_TOOLS = Path() / "prebuilts" / "kernel-build-tools" / "linux-x86"

PREBUILT_BIN_PATH = KERNEL_BUILD_TOOLS / "bin"
PREBUILT_LIB64_PATH = KERNEL_BUILD_TOOLS / "lib64"

DEPMOD_PATH = PREBUILT_BIN_PATH / "depmod"

CONFIG_FILE_NAME = "modules.conf"

DUMMY_KERNEL_VERSION = "0.0"

class LoadStage(Enum):
	"""
	Enumeration of module load stages.
	"""

	EARLY = "early"
	"""
	Modules loaded with first stage init, contained in ramdisk or vendor_boot.
	"""

	LATE = "late"
	"""
	Modules loaded with second stage init, contained in system or vendor.
	"""

	RECOVERY = "recovery"
	"""
	Modules loaded in recovery mode, contained in recovery image.
	"""

class ModuleMatch:
	def __init__(self) -> None:
		pass

class Configuration:
	def __init__(self) -> None:
		pass

def main():
	arg_parser = ArgumentParser(
		description="Generate a list of modules from a given input file",
	)
	arg_parser.add_argument(
		"modprobe_dir",
		help=f"Path to the modprobe directory that contains the {CONFIG_FILE_NAME} file and the modprobe files",
		type=Path,
	)
	arg_parser.add_argument(
		"kernel_out_dir",
		help="Path to the kernel output directory where the modules are located",
		type=Path,
	)

	args = arg_parser.parse_args()

	modprobe_dir: Path = args.modprobe_dir
	kernel_out_dir: Path = args.kernel_out_dir

	# Check that the file is in the right context
	script_path = Path(__file__).resolve()
	aosp_root_dir = script_path.parents[4]

	assert modprobe_dir.is_dir(), f"{modprobe_dir} is not a valid directory"
	assert kernel_out_dir.is_dir(), f"{kernel_out_dir} is not a valid directory"

	config_path = modprobe_dir / CONFIG_FILE_NAME
	assert config_path.is_file(), f"{config_path} does not exist"

	modules_order_path = kernel_out_dir / "modules.order"
	assert modules_order_path.is_file(), f"{modules_order_path} does not exist"

	modules_order = [
		Path(line).with_suffix(".ko")
		for line
		in modules_order_path.read_text().splitlines()
	]
	modules_order_name = [line.name for line in modules_order]

	stage_to_modules: Dict[LoadStage, List[str]] = {
		load_stage: [] for load_stage in LoadStage
	}

	with TemporaryDirectory() as temp_dir:
		temp_path = Path(temp_dir)

		# Create a temporary directory to hold the modules
		temp_modules_path = temp_path / "lib" / "modules" / DUMMY_KERNEL_VERSION
		temp_modules_path.mkdir(parents=True)

		# Copy all modules to the temporary directory
		for module_path in modules_order:
			src_path = kernel_out_dir / module_path
			dst_path = temp_modules_path / module_path.name

			assert src_path.is_file(), f"Module {src_path} does not exist"

			shutil.copy(src_path, dst_path)

		# Run depmod to generate the modules.dep file
		# Also use LD_LIBRARY_PATH to point to the prebuilt lib64 directory
		env = {"LD_LIBRARY_PATH": str(PREBUILT_LIB64_PATH)}
		depmod_cmd = [
			str(aosp_root_dir / DEPMOD_PATH),
			"-a",
			"-e",
			"-b", str(temp_path),
			"-F", str(kernel_out_dir / "System.map"),
			"-E", str(kernel_out_dir / "Module.symvers"),
			DUMMY_KERNEL_VERSION,
		]
		depmod_result = subprocess.run(depmod_cmd, env=env, capture_output=True, text=True)
		assert depmod_result.returncode == 0, f"depmod failed: {depmod_result.stderr}"

		modules_dep_path = temp_modules_path / "modules.dep"
		assert modules_dep_path.is_file(), f"{modules_dep_path} does not exist"

		# Parse the modules.dep file
		modules_dep: Dict[str, List[str]] = {}
		for line in modules_dep_path.read_text().splitlines():
			module, deps_str = line.split(":", 1)
			deps = [
				Path(dep.strip()).name
				for dep in deps_str.strip().split(" ")
			]

			modules_dep[Path(module).name] = deps

		# List all files in the temporary directory
		for module_file in temp_modules_path.iterdir():
			if module_file.suffix == ".ko":
				continue
			print(f"Found module: {module_file.name}")

	# Write to modules.load.* and modules.include.*
	for stage, modules in stage_to_modules.items():
		# modules.include.*
		modules_include_path = modprobe_dir / f"modules.include.{stage.value}"
		modules_include_list = sorted(modules)

		if modules_include_list:
			with modules_include_path.open("w") as f:
				for module in modules_include_list:
					f.write(f"{module}\n")
		else:
			modules_include_path.unlink(missing_ok=True)

		# modules.load.*
		if stage != LoadStage.RECOVERY:
			modules_load_path = modprobe_dir / f"modules.load.{stage.value}"
			modules_load_list = sorted(modules, key=modules_order_name.index)

			if modules_load_list:
				with modules_load_path.open("w") as f:
					for module in modules_load_list:
						f.write(f"{module}\n")
			else:
				modules_load_path.unlink(missing_ok=True)

if __name__ == "__main__":
	main()
