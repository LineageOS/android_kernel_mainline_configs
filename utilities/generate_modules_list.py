#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: The LineageOS Project
# SPDX-License-Identifier: Apache-2.0
#

from argparse import ArgumentParser
from enum import Enum
import fnmatch
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from typing import Callable, Dict, List, Optional, Set

KERNEL_BUILD_TOOLS = Path() / "prebuilts" / "kernel-build-tools" / "linux-x86"

PREBUILT_BIN_PATH = KERNEL_BUILD_TOOLS / "bin"
PREBUILT_LIB64_PATH = KERNEL_BUILD_TOOLS / "lib64"

DEPMOD_PATH = PREBUILT_BIN_PATH / "depmod"

CONFIG_FILE_NAME = "modules.conf"

DUMMY_KERNEL_VERSION = "0.0"

class ModuleLocation(Enum):
	"""
	Enumeration of module location.
	"""

	RECOVERY = "recovery"
	"""
	Modules loaded in recovery mode, contained in recovery ramdisk.
	"""

	INITRAMFS = "initramfs"
	"""
	Modules loaded with first stage init, contained in ramdisk.
	"""

	VENDOR = "vendor"
	"""
	Modules loaded with second stage init, contained in vendor_dlkm.
	"""

	SYSTEM = "system"
	"""
	Modules that aren't device-specific, contained in system_dlkm.
	"""

ALLOWED_DEPENDENCY_LOCATIONS: Dict[ModuleLocation, Set[ModuleLocation]] = {
	ModuleLocation.RECOVERY: {
		ModuleLocation.RECOVERY,
	},
	ModuleLocation.INITRAMFS: {
		ModuleLocation.INITRAMFS,
	},
	ModuleLocation.VENDOR: {
		ModuleLocation.INITRAMFS,
		ModuleLocation.VENDOR,
		ModuleLocation.SYSTEM,
	},
	ModuleLocation.SYSTEM: {
		ModuleLocation.SYSTEM,
	},
}
"""
Mapping of allowed dependency locations for each module location.
"""

class SoftDepOrder(Enum):
	PRE = "pre"
	POST = "post"

class Module:
	def __init__(
		self,
		path: Path,
		deps: Optional[Set[str]] = None,
		softdeps: Optional[Dict[str, SoftDepOrder]] = None,
		location: Optional[ModuleLocation] = None,
		include_in_recovery: bool = False,
		load: bool = False,
	) -> None:
		self.path = path
		self.deps = deps or set()
		self.softdeps = softdeps or {}
		self.location = location
		self.include_in_recovery = include_in_recovery
		self.load = load

		assert not self.path.is_absolute(), f"Module path must not be absolute: {self.path}"
		assert self.path.suffix == ".ko", f"Module path must end with .ko: {self.path}"

	def get_name(self) -> str:
		"""
		Get the module name.
		"""
		return self.path.name

class Option:
	class Location(Enum):
		NONE = "none"
		INITRAMFS = "initramfs"
		VENDOR = "vendor"
		SYSTEM = "system"

	def __init__(
		self,
		pattern: str,
		location: Optional[Location] = None,
		include_in_recovery: Optional[bool] = None,
		load: Optional[bool] = None,
	) -> None:
		self.pattern = pattern
		self.location = location
		self.include_in_recovery = include_in_recovery
		self.load = load

		self._match_lambda: Callable[[Module], bool]
		if self.pattern == "*":
			self._match_lambda = lambda _: True
		elif "/" in self.pattern:
			self._match_lambda = lambda module: fnmatch.fnmatchcase(module.path.as_posix(), self.pattern)
		else:
			self._match_lambda = lambda module: fnmatch.fnmatchcase(module.get_name(), self.pattern)

	def matches(self, module: Module) -> bool:
		"""
		Check if the option matches the given module.
		"""
		return self._match_lambda(module)

	@staticmethod
	def from_line(line: str) -> "Option":
		parts = line.split()
		assert len(parts) == 2, f"Invalid option line: {line}"

		pattern = parts[0]
		location: Optional[Option.Location] = None
		include_in_recovery: Optional[bool] = None
		load: Optional[bool] = None

		for flag in parts[1].split(","):
			flag = flag.strip()

			if flag.startswith("location="):
				location_str = flag.split("=", 1)[1]
				try:
					location = Option.Location(location_str)
				except ValueError:
					raise ValueError(f"Unknown location in option line: {location_str}")
			elif flag.startswith("recovery="):
				recovery_str = flag.split("=", 1)[1]
				if recovery_str == "true":
					include_in_recovery = True
				elif recovery_str == "false":
					include_in_recovery = False
				else:
					raise ValueError(f"Invalid recovery value in option line: {recovery_str}")
			elif flag.startswith("load="):
				load_str = flag.split("=", 1)[1]
				if load_str == "true":
					load = True
				elif load_str == "false":
					load = False
				else:
					raise ValueError(f"Invalid load value in option line: {load_str}")
			else:
				raise ValueError(f"Unknown flag in option line: {flag}")

		return Option(
			pattern=pattern,
			location=location,
			include_in_recovery=include_in_recovery,
			load=load,
		)

class Configuration:
	def __init__(
		self,
		options: Optional[List[Option]] = None,
	) -> None:
		self.options = options or []

	@staticmethod
	def from_file(config_path: Path) -> "Configuration":
		options: List[Option] = []

		for line in config_path.read_text().splitlines():
			line = line.strip()

			# Skip empty lines and comments
			if not line or line.startswith("#"):
				continue

			option = Option.from_line(line)
			options.append(option)

		return Configuration(options)

def get_recursive_dependencies(
	modules: Dict[str, Module],
	module_name: str,
	seen: Optional[Set[str]] = None,
) -> Set[str]:
	"""
	Get all recursive dependencies for a given module.
	"""
	if seen is None:
		seen = set()

	if module_name not in modules:
		return seen

	module = modules[module_name]

	for dep in module.deps:
		if dep not in seen:
			seen.add(dep)
			get_recursive_dependencies(modules, dep, seen)

	return seen

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

	configuration = Configuration.from_file(config_path)

	modules_order_path = kernel_out_dir / "modules.order"
	assert modules_order_path.is_file(), \
		f"{modules_order_path} does not exist, have you built the kernel?"

	modules_order = [
		Path(line).with_suffix(".ko")
		for line
		in modules_order_path.read_text().splitlines()
	]

	modules: Dict[str, Module] = {
		module_path.name: Module(path=module_path)
		for module_path
		in modules_order
	}

	with TemporaryDirectory() as temp_dir:
		temp_path = Path(temp_dir)

		# Create a temporary directory to hold the modules
		temp_modules_path = temp_path / "lib" / "modules" / DUMMY_KERNEL_VERSION
		temp_modules_path.mkdir(parents=True)

		# Copy all modules to the temporary directory
		for module_name, module in modules.items():
			src_path = kernel_out_dir / module.path
			dst_path = temp_modules_path / module_name

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

		# Parse the modules.dep file
		modules_dep_path = temp_modules_path / "modules.dep"
		assert modules_dep_path.is_file(), f"{modules_dep_path} does not exist"

		for line in modules_dep_path.read_text().splitlines():
			module_path, deps_str = line.split(":", 1)

			module_name = Path(module_path).name

			assert module_name in modules, f"Module {module_name} not found in modules list"

			deps: Set[str] = set()
			for dep in deps_str.strip().split():
				dep_name = Path(dep).name
				assert dep_name in modules, f"Dependency {dep_name} not found in modules list"
				deps.add(dep_name)

			modules[module_name].deps.update(deps)

		# Parse the modules.softdep file if it exists
		modules_softdep_path = temp_modules_path / "modules.softdep"
		if modules_softdep_path.is_file():
			pass # TODO

		# Parse the modules.weakdep file if it exists
		modules_weakdep_path = temp_modules_path / "modules.weakdep"
		if modules_weakdep_path.is_file():
			pass # TODO

	for module in modules.values():
		# Apply configuration options
		for option in configuration.options:
			if not option.matches(module):
				continue

			if option.location is not None:
				if option.location == Option.Location.NONE:
					module.location = None
				elif option.location == Option.Location.INITRAMFS:
					module.location = ModuleLocation.INITRAMFS
				elif option.location == Option.Location.VENDOR:
					module.location = ModuleLocation.VENDOR
				elif option.location == Option.Location.SYSTEM:
					module.location = ModuleLocation.SYSTEM
				else:
					raise ValueError(f"Unknown location in option: {option.location}")

			if option.include_in_recovery is not None:
				module.include_in_recovery = option.include_in_recovery

			if option.load is not None:
				module.load = option.load

	# Load stage to modules to whether or not it needs to be loaded
	stage_to_modules_to_load: Dict[ModuleLocation, Dict[str, bool]] = {
		location: {} for location in ModuleLocation
	}

	for module in modules.values():
		# Skip modules that are not assigned to any location and not included in recovery
		if not module.location and not module.include_in_recovery:
			continue

		module_name = module.get_name()
		deps = get_recursive_dependencies(modules, module_name)

		if module.location:
			stage_to_modules_to_load[module.location][module_name] = module.load

			for dep in deps:
				dep_module = modules[dep]

				if module.location != dep_module.location:
					# Make sure the dependency can be honored
					assert dep_module.location in ALLOWED_DEPENDENCY_LOCATIONS[module.location], \
						f"Module {module.path} living in {module.location} cannot depend on" \
						f" {dep_module.path} living in {dep_module.location}"

					stage_to_modules_to_load[module.location][dep] = False

		if module.include_in_recovery:
			stage_to_modules_to_load[ModuleLocation.RECOVERY][module_name] = module.load

			for dep in deps:
				dep_module = modules[dep]

				if not dep_module.include_in_recovery:
					stage_to_modules_to_load[ModuleLocation.RECOVERY][dep] = False

	# Write to modules.include.* and modules.load.*
	modules_order_by_name = [line.name for line in modules_order]
	for stage, stage_modules in stage_to_modules_to_load.items():
		# modules.include.*
		modules_include_path = modprobe_dir / f"modules.include.{stage.value}"
		modules_include_list = sorted(stage_modules.keys())

		if modules_include_list:
			with modules_include_path.open("w") as f:
				for module in modules_include_list:
					f.write(f"{module}\n")
		else:
			modules_include_path.unlink(missing_ok=True)

		modules_load_path = modprobe_dir / f"modules.load.{stage.value}"
		modules_load_list = sorted(
			[module for module, load in stage_modules.items() if load],
			key=modules_order_by_name.index,
		)

		if modules_load_list:
			with modules_load_path.open("w") as f:
				for module in modules_load_list:
					f.write(f"{module}\n")
		else:
			modules_load_path.unlink(missing_ok=True)

if __name__ == "__main__":
	main()
