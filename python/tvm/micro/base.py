# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""Base definitions for MicroTVM"""

import tvm
import tvm._ffi

from tvm.contrib import util as _util
from tvm.contrib import cc as _cc

# all sections that comprise a device's memory layout, in order from lowest
# starting address to highest
DEVICE_SECTIONS = [
    "text",
    "rodata",
    "data",
    "bss",
    "args",
    "heap",
    "workspace",
    "stack",
]


class LibType(Enum):
    """Enumeration of library types that can be compiled and loaded onto a device"""

    # library to be used as a MicroTVM runtime
    RUNTIME = 0
    # library to be used as an operator
    OPERATOR = 1


class Session:
    """MicroTVM Device Session

    Parameters
    ----------
    config : dict
        configuration for this session (as generated by
        `tvm.micro.device.host.default_config()`, for example)

    Example
    --------
    .. code-block:: python

      c_mod = ...  # some module generated with "c" as the target
      dev_config = micro.device.arm.stm32f746xx.default_config('127.0.0.1', 6666)
      with tvm.micro.Session(dev_config) as sess:
          micro_mod = sess.create_micro_mod(c_mod)
    """

    def __init__(self, config):
        self._check_system()
        # TODO(weberlo): add config validation

        # grab a binutil instance from the ID in the config
        dev_funcs = tvm.micro.device.get_device_funcs(config["device_id"])
        self.toolchain_prefix = config["toolchain_prefix"]
        self.mem_layout = config["mem_layout"]
        self.word_size_bits = config["word_size_bits"]
        self.thumb_mode = config["thumb_mode"]
        self.use_device_timer = config["use_device_timer"]
        self.comms_method = config["comms_method"]

        # First, find and compile runtime library.
        runtime_src_path = os.path.join(get_micro_host_driven_dir(), "utvm_runtime.c")
        tmp_dir = _util.tempdir()
        runtime_obj_path = tmp_dir.relpath("utvm_runtime.obj")
        options = ["-I{}".format(get_micro_host_driven_dir())]
        dev_funcs["create_micro_lib"](
            runtime_obj_path, runtime_src_path, LibType.RUNTIME, options=options
        )

        comms_method = config["comms_method"]
        if comms_method == "openocd":
            server_addr = config["server_addr"]
            server_port = config["server_port"]
        elif comms_method == "host":
            server_addr = ""
            server_port = 0
        else:
            raise RuntimeError(f"unknown communication method: f{self.comms_method}")

        assert all(
            map(lambda sec: sec in self.mem_layout, DEVICE_SECTIONS)
        ), "not all sections have an assigned memory layout"
        self.module = _CreateSession(
            comms_method,
            runtime_obj_path,
            self.toolchain_prefix,
            self.mem_layout["text"].get("start", 0),
            self.mem_layout["text"]["size"],
            self.mem_layout["rodata"].get("start", 0),
            self.mem_layout["rodata"]["size"],
            self.mem_layout["data"].get("start", 0),
            self.mem_layout["data"]["size"],
            self.mem_layout["bss"].get("start", 0),
            self.mem_layout["bss"]["size"],
            self.mem_layout["args"].get("start", 0),
            self.mem_layout["args"]["size"],
            self.mem_layout["heap"].get("start", 0),
            self.mem_layout["heap"]["size"],
            self.mem_layout["workspace"].get("start", 0),
            self.mem_layout["workspace"]["size"],
            self.mem_layout["stack"].get("start", 0),
            self.mem_layout["stack"]["size"],
            self.word_size_bits,
            self.thumb_mode,
            self.use_device_timer,
            server_addr,
            server_port,
            config.get("debug_func"),
        )
        self._enter = self.module["enter"]
        self._exit = self.module["exit"]
        self.get_last_batch_time = self.module["get_last_batch_time"]
        self.get_last_batch_cycles = self.module["get_last_batch_cycles"]

    def _check_system(self):
        """Check if the user's system is supported by MicroTVM.

        Raises error if not supported.
        """
        if not sys.platform.startswith("linux"):
            raise RuntimeError("MicroTVM is currently only supported on Linux")
        # TODO(weberlo): Add 32-bit support.
        # It's primarily the compilation pipeline that isn't compatible.
        if sys.maxsize <= 2 ** 32:
            raise RuntimeError("MicroTVM is currently only supported on 64-bit host platforms")

    def __enter__(self):
        self._enter()
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self._exit()


def _calc_max_workspace_usage(src):
    # TODO factor in alignment to the calculation (alloc sizes will be aligned up to the word size)
    alloc_re = re.compile(
        r".*\* ?(.+) = (\(.+\))? TVMBackendAllocWorkspace\(.+, .+, \(uint64_t\)(.+), .+, .+\).*"
    )
    free_re = re.compile(r".*if \(TVMBackendFreeWorkspace\(.+, .+, (\(void\*\))? (.+)\) != 0\) {.*")
    max_usage = 0
    alloc_map = {}
    for line in src.split("\n"):
        if line.strip().startswith("//"):
            continue
        match = alloc_re.match(line)
        if match is not None:
            alloc_map[match.group(1)] = int(match.group(3))
            max_usage = max(max_usage, sum(alloc_map.values()))
        else:
            match = free_re.match(line)
            if match is not None:
                print(alloc_map)
                del alloc_map[match.group(2)]
    return max_usage


def create_micro_mod(
    c_mod, dev_config, lib_src_paths=None, lib_headers=None, lib_include_paths=None
):
    """Produces a micro module from a given module.

    Parameters
    ----------
    c_mod : tvm.module.Module
        module with "c" as its target backend

    lib_src_paths: TODO
        TODO

    lib_headers: TODO
        TODO

    lib_include_paths: TODO
        TODO

    Return
    ------
    micro_mod : tvm.module.Module
        micro module for the target device
    """
    temp_dir = _util.tempdir()
    lib_obj_path = temp_dir.relpath("dev_lib.obj")
    # TODO use dev config to dispatch on the type of C codegen to run through
    # (e.g., CodeGenCArm, CodeGenCHost, CodeGenCRiscV)
    c_mod.export_library(
        lib_obj_path,
        fcompile=cross_compiler(
            dev_config,
            LibType.OPERATOR,
            lib_src_paths=lib_src_paths,
            lib_headers=lib_headers,
            lib_include_paths=lib_include_paths,
        ),
    )
    micro_mod = tvm.runtime.load_module(lib_obj_path)
    return micro_mod


def cross_compiler(
    dev_config, lib_type, lib_src_paths=None, lib_headers=None, lib_include_paths=None
):
    """Create a cross compile function that wraps `create_lib` for a `Binutil` instance.

    For use in `tvm.runtime.Module.export_library`.

    Parameters
    ----------
    create_micro_lib : func
        function for creating MicroTVM libraries for a specific device (e.g.,
        `tvm.micro.device.get_device_funcs('arm.stm32f746xx')['create_micro_lib']`)

    lib_type : micro.LibType
        whether to compile a MicroTVM runtime or operator library

    lib_src_paths: TODO
        TODO

    lib_headers: TODO
        e.g., `['cmsis_gcc.h', 'arm_math.h']`

    lib_include_paths: TODO
        TODO

    Return
    ------
    func : Callable[[str, str, Optional[str]], None]
        cross compile function taking a destination path for the object file
        and a path for the input source file.

    Example
    --------
    .. code-block:: python

      c_mod = ...  # some module generated with "c" as the target
      fcompile = tvm.micro.cross_compiler(dev_config, LibType.OPERATOR)
      c_mod.export_library('dev_lib.obj', fcompile=fcompile)
    """
    assert (lib_headers is None) == (
        lib_include_paths is None
    ), "must specify both `lib_headers` and `lib_include_paths` or neither"

    if lib_src_paths is None:
        lib_src_paths = []
    if lib_include_paths is None:
        lib_include_paths = []
    include_options = []
    for include_path in lib_include_paths:
        include_options.append("-I")
        include_options.append(include_path)
    create_micro_lib = tvm.micro.device.get_device_funcs(dev_config["device_id"])[
        "create_micro_lib"
    ]
    mem_layout = dev_config["mem_layout"]

    def compile_func(obj_path, src_path, **kwargs):
        if isinstance(obj_path, list):
            obj_path = obj_path[0]
        if isinstance(src_path, list):
            src_path = src_path[0]
        options = kwargs.get("options", [])
        options += include_options

        # check that workspace allocations don't exceed available workspace memory
        with open(src_path) as f:
            src_contents = f.read()
            max_ws_usage = _calc_max_workspace_usage(src_contents)
            available_mem = mem_layout["workspace"]["size"]
            if max_ws_usage > available_mem:
                raise RuntimeError(
                    f"workspace allocations in library ({max_ws_usage}) "
                    f"exceed available memory ({available_mem})"
                )
        # inject headers into new source path, if requested
        if lib_headers:
            headers_to_inject = "\n".join(map(lambda s: f"#include <{s}>", lib_headers)) + "\n"
            new_src_contents = headers_to_inject + src_contents
            tmp_dir = _util.tempdir()
            src_path = tmp_dir.relpath(os.path.basename(src_path))
            with open(src_path, "w") as f:
                f.write(new_src_contents)

        create_micro_lib(obj_path, src_path, lib_type, options, lib_src_paths=lib_src_paths)

    return _cc.cross_compiler(compile_func, output_format="obj")


def get_micro_host_driven_dir():
    """Get directory path for uTVM host-driven runtime source files.

    Return
    ------
    micro_device_dir : str
        directory path
    """
    micro_dir = os.path.dirname(os.path.realpath(os.path.expanduser(__file__)))
    micro_host_driven_dir = os.path.join(
        micro_dir, "..", "..", "..", "src", "runtime", "micro", "host_driven"
    )
    return micro_host_driven_dir


def get_micro_device_dir():
    """Get directory path for parent directory of device-specific source files

    Return
    ------
    micro_device_dir : str
        directory path
    """
    micro_dir = os.path.dirname(os.path.realpath(os.path.expanduser(__file__)))
    micro_device_dir = os.path.join(
        micro_dir, "..", "..", "..", "src", "runtime", "micro", "device"
    )
    return micro_device_dir


tvm._ffi._init_api("tvm.micro", "tvm.micro.base")
