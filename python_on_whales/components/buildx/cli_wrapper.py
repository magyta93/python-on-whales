from __future__ import annotations

import datetime as dt
import json
import tempfile
from enum import Enum
from pathlib import Path
from typing import (
Any,
Dict,
Iterable,
Iterator,
List,
Literal,
Optional,
Tuple,
Union,
overload,
)

import python_on_whales.components.image.cli_wrapper
from python_on_whales.client_config import (
ClientConfig,
DockerCLICaller,
ReloadableObject,
)
from python_on_whales.components.buildx.imagetools.cli_wrapper import ImagetoolsCLI
from python_on_whales.components.buildx.models import BuilderInspectResult, BuilderNode
from python_on_whales.utils import (
ValidPath,
format_mapping_for_cli,
run,
stream_stdout_and_stderr,
to_list,
)


class GetImageMethod(Enum):
TAG = 1
IIDFILE = 2


class Builder(ReloadableObject):
def __init__(
self,
client_config: ClientConfig,
reference: str,
is_immutable_id=False,
):
super().__init__(client_config, "name", reference, is_immutable_id)

def __enter__(self):
return self

def __exit__(self, exc_type, exc_val, exc_tb):
self.remove()

def _fetch_and_parse_inspect_result(self, reference: str) -> BuilderInspectResult:
full_cmd = self.docker_cmd + ["buildx", "ls", "--format", "{{json . }}"]
inspect_str = run(full_cmd)
lines = inspect_str.splitlines()
for line in lines:
builder_inspect_result = BuilderInspectResult(**json.loads(line))
if builder_inspect_result.name == reference:
return builder_inspect_result
raise ValueError(
f"Could not find a builder with the name {reference}, this should "
f"never happen unless you are quickly creating and deleting builders. "
f"Please open an issue at https://github.com/gabrieldemarmiesse/python-on-whales/issues"
)

@property
def name(self) -> str | None:
return self._get_immutable_id()

@property
def driver(self) -> str | None:
return self._get_inspect_result().driver

@property
def last_activity(self) -> dt.datetime | None:
return self._get_inspect_result().last_activity

@property
def dynamic(self) -> bool | None:
return self._get_inspect_result().dynamic

@property
def nodes(self) -> List[BuilderNode] | None:
return self._get_inspect_result().nodes

def __repr__(self):
return f"python_on_whales.Builder(name='{self.name}', driver='{self.driver}')"

def remove(self):
"""Removes this builder. After this operation the builder cannot be used anymore.

If you use the builder as a context manager, it will call this function when
you exit the context manager.

```python
from python_on_whales import docker

buildx_builder = docker.buildx.create(use=True)
with buildx_builder:
docker.build(".")

# now the variable buildx_builder is not usable since we're out of the context manager.
# the .remove() method was called behind the scenes
# since it was the current builder, 'default' is now the current builder.
```

"""
BuildxCLI(self.client_config).remove(self)


ValidBuilder = Union[str, Builder]


class BuildxCLI(DockerCLICaller):
def __init__(self, client_config: ClientConfig):
super().__init__(client_config)
self.imagetools = ImagetoolsCLI(self.client_config)

def bake(
self,
targets: Union[str, List[str]] = [],
allow: Union[str, List[str]] = [],
builder: Optional[ValidBuilder] = None,
files: Union[ValidPath, List[ValidPath]] = [],
load: bool = False,
cache: bool = True,
print: bool = False,
progress: Literal["auto", "plain", "tty", False] = "auto",
pull: bool = False,
push: bool = False,
set: Dict[str, str] = {},
variables: Dict[str, str] = {},
metadata_file: Optional[ValidPath] = None,
stream_logs: bool = False,
remote_definition: Union[str, None] = None,
) -> Union[Dict[str, Dict[str, Dict[str, Any]]], Iterator[str]]:
"""Bake is similar to make, it allows you to build things declared in a file.

For example it allows you to build multiple docker image in parallel.

The CLI docs is [here](https://github.com/docker/buildx#buildx-bake-options-target)
and it contains a lot more information.

Parameters:
targets: Targets or groups of targets to build.
allow: List of extra privileges granted to the build.
Eg `allow=["fs.read=/home/my_user/.netrc", "network.host"]`
builder: The builder to use.
files: Build definition file(s)
load: Shorthand for `set=["*.output=type=docker"]`
cache: Whether to use the cache or not.
print: Do nothing, just returns the config.
progress: Set type of progress output (`"auto"`, `"plain"`, `"tty"`,
or `False`). Use plain to keep the container output on screen
pull: Always try to pull the newer version of the image
push: Shorthand for `set=["*.output=type=registry"]`
set: A list of overrides in the form `"targetpattern.key=value"`.
variables: A dict containing the values of the variables defined in the
hcl file. See <https://github.com/docker/buildx#hcl-variables-and-functions>
metadata_file: Write build results metadata to the given file
remote_definition: Remote context in which to find bake files

# Returns
The configuration used for the bake (files merged + override with
the arguments used in the function). It's the loaded json you would
obtain by running `docker buildx bake --print --load my_target` if
your command was `docker buildx bake --load my_target`. Some example here.


```python
from python_on_whales import docker

# returns the config used and runs the builds
config = docker.buildx.bake(["my_target1", "my_target2"], load=True)
assert config == {
"target": {
"my_target1": {
"context": "./",
"dockerfile": "Dockerfile",
"tags": ["pretty_image1:1.0.0"],
"target": "out1",
"output": ["type=docker"]
},
"my_target2": {
"context": "./",
"dockerfile": "Dockerfile",
"tags": ["pretty_image2:1.0.0"],
"target": "out2",
"output": ["type=docker"]
}
}
}

# returns the config only, doesn't run the builds
config = docker.buildx.bake(["my_target1", "my_target2"], load=True, print=True)
```
"""
full_cmd = self.docker_cmd + ["buildx", "bake"]

full_cmd.add_flag("--no-cache", not cache)
full_cmd.add_args_iterable_or_single("--allow", allow)
full_cmd.add_simple_arg("--builder", builder)
full_cmd.add_flag("--load", load)
full_cmd.add_flag("--pull", pull)
full_cmd.add_flag("--push", push)
full_cmd.add_flag("--print", print)
if progress != "auto" and isinstance(progress, str):
full_cmd += ["--progress", progress]
for file in to_list(files):
full_cmd.add_simple_arg("--file", file)
full_cmd.add_args_iterable_or_single("--set", format_mapping_for_cli(set))
if remote_definition is not None:
full_cmd.append(remote_definition)
if metadata_file is not None:
full_cmd.add_simple_arg("--metadata-file", metadata_file)
targets = to_list(targets)
env = dict(variables)
if print:
if stream_logs:
ValueError(
"Getting the config of the bake and streaming "
"logs at the same time is not possible."
)
return json.loads(run(full_cmd + targets, env=env))
elif stream_logs:
return stream_buildx_logs(full_cmd + targets, env=env)
else:
run(full_cmd + targets, capture_stderr=progress is False, env=env)
return json.loads(run(full_cmd + ["--print"] + targets, env=env))

def build(
self,
context_path: ValidPath,
add_hosts: Dict[str, str] = {},
allow: List[str] = [],
attest: Optional[Dict[str, str]] = None,
build_args: Dict[str, str] = {},
build_contexts: Dict[str, Union[str, ValidPath]] = {},
builder: Optional[ValidBuilder] = None,
cache: bool = True,
# TODO: cache_filters
cache_from: Union[str, Dict[str, str], List[Dict[str, str]], None] = None,
cache_to: Union[str, Dict[str, str], None] = None,
# TODO: cgroup_parent
file: Optional[ValidPath] = None,
labels: Dict[str, str] = {},
load: bool = False,
metadata_file: Optional[ValidPath] = None,
network: Optional[str] = None,
output: Dict
