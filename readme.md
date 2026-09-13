

Dependencies:
    python3
    python3-pip
    python3-paho-mqtt
    python3-yaml
    smartmontools
    lm-sensors
    skopeo        (required for the docker module's image-update checks)

apt install python3 python3-pip python3-paho-mqtt python3-yaml smartmontools lm-sensors

apt install skopeo   (docker module image-update checks)


# Docker monitoring module (enabled by default)

On by default (`docker.enabled: true`); it only registers entities on hosts
where at least one `docker.compose_files` path exists, so non-docker hosts
stay clean. Set `docker.enabled: false` to disable it entirely. It reads the
compose project(s) listed in `docker.compose_files` (default
`/container-data/compose.yaml`) and publishes:

  - binary_sensor "Docker monitoring enabled" (diagnostic) - always
      registered, even when docker.enabled is false or no compose file was
      found, so a Lovelace card can condition on it directly to show/hide
      docker-related cards. ON only when docker.enabled is true AND a
      compose file was found (i.e. exactly when the entities below exist).
  - sensor "Docker containers up" (diagnostic)  -> number of running containers
  - binary_sensor "Docker containers with errors" (diagnostic, device_class
      problem) ON when a container is unhealthy / restarting / OOM-killed /
      exited non-zero / restart-looping. JSON attributes list the containers
      and the reason for each.
  - one HA update entity (diagnostic) per in-use image, entity_id
      update.container_<slug> (e.g. update.container_nginx_latest): installed
      vs registry image digest. Shows "update available" when they differ.
      If docker.allow_install is true the entity's Install button runs, for
      that image's compose file and the service(s) that use it:
        docker compose -f <file> pull <service...> && docker compose -f <file> up -d <service...>
      (falls back to updating every service in the file if the service name
      could not be determined). The entity reports in_progress while that
      command is running, so HA shows an "Installing" indicator instead of
      giving no feedback after the button is pressed.
  - per-container resource sensors (not categorized - regular sensors, so
      they group with your other measurements rather than under Diagnostic),
      when docker.stats.enabled, entity_id sensor.<host>_container_<container>_<metric>
      (e.g. sensor.hpc_test_1_container_nginx_cpu):
      "<container> CPU" %, "<container> Memory" MB, "<container> Memory %",
      "<container> Net in/out" MB/s, "<container> Disk read/write" MB/s.
      Metric families are toggled by docker.stats.cpu / memory / network / disk;
      individual containers via docker.stats.containers.<name>.enabled (which
      overrides docker.stats.enabled, so you can also opt in a single container
      while the global toggle is off).

Config keys (defaults shown):

  docker:
    enabled: true
    compose_files:
      - "/container-data/compose.yaml"   # string or list
    update_interval: 60                  # container state poll, seconds
    image_check_interval: 3600           # registry digest checks, seconds
    restart_loop_threshold: 3            # restart-count rise to flag a loop
    docker_binary: "docker"              # e.g. "sudo docker" if not in docker group
    allow_install: false                 # expose Install button on image update entities
    containers_up_sensor_name: "Docker containers up"
    containers_error_sensor_name: "Docker containers with errors"
    image_update_prefix: ""              # optional per-image entity name prefix
    stats:
      enabled: false                     # global per-container resource monitoring
      update_interval: 30                # not faster than docker.update_interval
      cpu: true
      memory: true
      network: true
      disk: true
      containers: {}                     # <name>: { enabled: true|false } overrides

Requirements: the docker CLI with the compose v2 plugin, and the agent's
user must have docker access (member of the `docker` group, or set
`docker_binary: "sudo docker"`). Image-update checks require `skopeo` -
without it, every image's update entity just reads "unavailable" instead of
guessing. (Earlier releases fell back to `docker buildx imagetools inspect` /
`docker manifest inspect` when skopeo was missing; that was removed because
those commands' digest for a multi-arch image is not reliably the same
digest RepoDigests records, so it could report every multi-arch image as
having an update available even when it didn't - skopeo's `.Digest` is the
one field verified to match.) Images that are locally built or pinned by
digest are skipped either way.


# Host update module (enabled by default)

On by default (`host_update.enabled: true`); set to `false` to disable.
Publishes:

  - one HA update entity "Host updates" (diagnostic): installed_version "0" vs
      latest_version "<count of available packages>". The Install button
      appears whenever host_update.allow_install is true (default true), and
      the entity reports in_progress for the duration of the install command
      so HA shows an "Installing" indicator. Since the default install
      command reboots the host, this process can be killed before it gets a
      chance to publish "done" - every (re)registration (startup and every
      device.refresh_entities cycle) force-clears in_progress unless an
      install is genuinely running in this process, so a reboot can never
      leave the entity stuck. Same guard on the docker per-image and
      Py-K93SYS update entities. Once the install command finishes, the
      script itself restarts (same as the "Restart Py-K93SYS" button) so
      every sensor re-checks from a clean process instead of the update
      entity re-reading its own possibly-stale cached data (e.g. a status
      command that hasn't re-run since the upgrade yet) - a no-op if the
      install command already rebooted the host.
  - sensor "Available updates" (diagnostic) -> "<count> updates available".
  - sensor "new_package_version_available" (diagnostic) -> comma-separated
      "PKG-CURRENT_VER>NEW_VER" entries, truncated to fit the HA state limit.
      Package names only - it does not include docker images.

`count` is the OS package count plus, when `include_docker_updates` is true
(default), however many docker per-image update entities currently report
"update available" (docker.enabled must be on and have found a compose
file). release_summary breaks the two back out, e.g. "3 package(s) and 2
docker image(s) can be upgraded." Set `include_docker_updates: false` to
report OS packages only.

The OS package count comes from `count_source` when set: the name of a
`commands.status.*` entry (e.g. the built-in `updates_available` sensor)
whose value is read from its published state and parsed for a leading
integer. That status command must be enabled. When `count_source` is empty
the module runs `check_cmd`.

Pressing Install runs one of two sources, chosen by the
`use_custom_install_cmd` switch:
  - false (default) -> the `command` of the `button:` entry named
    `install_button_name` (default "Update and reboot") - this is how it
    picks up your existing "Update and reboot" button's command (apt
    upgrade + docker compose pull/up + reboot) without duplicating it, so
    by default pressing Install on this entity reboots the host once the
    update finishes. Point install_button_name at your non-rebooting
    "Update" button if you don't want that.
  - true -> `install_cmd` directly, ignoring button: entirely.
Either way, if the selected source resolves to nothing, it falls back to a
package-manager default (apt/dnf/yum/zypper/pacman, apt if none detected).
check_cmd / list_cmd always use the package-manager default, they never
consult button: or install_cmd. Defaults:
  check_cmd   -> apt-get -s upgrade | grep -c '^Inst '
  list_cmd    -> apt-get -s upgrade, formatted as PKG-CURRENT>NEW per line
  install_cmd -> sudo apt update && sudo apt upgrade -y
(dnf/yum/zypper/pacman have equivalents; only apt's list_cmd includes the
current version.)

Config keys (defaults shown):

  host_update:
    enabled: true
    name: "Host updates"
    count_sensor_name: "Available updates"
    packages_sensor_name: "New package version available"
    update_interval: 3600
    count_source: ""     # e.g. "updates_available" -> use that commands.status sensor's value
    include_docker_updates: true  # add the count of docker images with an update available
    check_cmd: ""        # prints an integer count; apt/dnf/yum/zypper/pacman default if empty
    list_cmd: ""         # prints "PKG-CUR>NEW" per line; package-manager default if empty
    install_cmd: ""      # used only when use_custom_install_cmd is true
    install_button_name: "Update and reboot"  # button:[].name; used when use_custom_install_cmd is false
    use_custom_install_cmd: false  # false = run the named button's command; true = run install_cmd
    allow_install: true
    device_class: ""     # set to "firmware" if you want that device class; leave empty if the
                          # entity doesn't show up (untested combination with entity_category)

docker.allow_install, host_update.allow_install, host_update.use_custom_install_cmd
and device.self_update_allow_install each also get an auto-generated HA
switch ("Docker Allow Install" / "Host Update Allow Install" / "Host Update
Use Custom Install Command" / "Py-K93SYS Allow Install") so you can flip
them from Home Assistant instead of editing config.yaml by hand - useful
since a config default changing in an update never retroactively changes a
value already written to your deployed config.yaml.

docker.stats.cpu / memory / network / disk each also get their own switch
("Docker Stats CPU State" / "... Memory State" / "... Network State" /
"... Disk State"), independent of the "Docker Stats State" switch for
docker.stats.enabled itself - so you can turn off just, say, network
monitoring across every container without touching CPU/memory/disk, or
docker.stats.containers.<name>.enabled's per-container override.

host_update.install_button_name and host_update.install_cmd each also get an
auto-generated HA text entity ("Host Update Install Button Name" / "Host
Update Install Command", config category) so you can pick a different
button:[].name or type a whole custom install command straight from Home
Assistant - same write-to-config.yaml-and-restart pattern as the interval
numbers and state switches above.

Every commands.status.<key>.cmd also gets its own text entity ("<Key>
Command", e.g. "Updates Available Command" for commands.status.updates_available),
so you can edit any status command's shell command straight from Home
Assistant too, alongside its existing State switch and Update Interval
number.

Every entity this agent forces a specific entity_id for gets it in the form
<host>_<name>, always starting with the device's slug (base.slugify(),
exposed as self.device_slug) so ids are easy to filter per-host in HA - the
three config-entity types (numbers, switches, texts) use <host>_config_<setting>
specifically, e.g. switch.hpc_test_1_config_docker_monitoring_state; plain
forced ids look like sensor.hpc_test_1_iowait, update.hpc_test_1_container_<slug>,
update.hpc_test_1_py_k93sys. This is set via the discovery payload's
default_entity_id key - MQTT discovery's older object_id key is deprecated
(removed in HA Core 2026.4) and no longer does this, so default_entity_id is
what every forced-entity-id entity in this agent uses. Entities that don't
force an id (most sensors) are still named by HA from their "name" field as
usual and won't include the host prefix unless the name itself does.

The resolved install command usually needs passwordless sudo for the
agent's user.


# Discovery settle delay, and retained state

Every state publish is retained by default now (base.HAMQTTBase.publish's
retain parameter defaults to True) - this is the real fix for entities
showing "unknown" after a restart. A sensor that only checks once an hour
(host_update, docker image updates, any commands.status.* entry) publishes
essentially once per process lifetime right after startup; if that single
publish is not retained and happens to land before Home Assistant finishes
processing the entity's discovery config and subscribing to its state_topic,
the value is gone - nothing resends it until the next hour-long interval.
With retain=True the broker holds the value and delivers it the moment HA
does subscribe, regardless of that race's timing.

On top of that, after every discovery registration pass at startup
(including the restart that "Refresh Py-K93SYS Entities" triggers after
wiping the old entities), the agent still waits
device.discovery_settle_delay seconds (default 2.0) before any loop starts
publishing state, narrowing the race further. Raise it if you still see
"unknown" values on a heavily loaded broker; set to 0 to disable.

  device:
    discovery_settle_delay: 2.0


# "Error" binary sensor (always on, general purpose)

One binary_sensor "Error" (diagnostic, device_class problem), entity_id
binary_sensor.error-ish, always registered - a general-purpose problem
flag any part of the codebase can raise later via self.set_error(True,
"reason") / self.set_error(False), with the reason exposed as a
json_attributes_topic attribute. It's cleared to OFF on every fresh
registration (startup, and each device.refresh_entities cycle) unless
something in this process has it raised right now, same pattern as the
update entities' in_progress guard.

Right now it's wired to fire whenever this process is about to go away for
a reason you'd want to know about:
  - the "Restart Py-K93SYS" button
  - a config number/switch/text entity change (which restarts the agent)
  - a host_update install (the default command can reboot the host)
  - a Py-K93SYS self-update install
  - any custom button: whose command contains "shutdown", "reboot",
    "poweroff" or "halt" (case-insensitive) - covers the Shutdown/Reboot/
    Update-and-reboot buttons out of the box
It is not raised for the "Refresh Py-K93SYS Entities" button, since that
button wipes all discovery (including this entity) before restarting -
nothing would be visible in HA before the entity disappears anyway.

Because the flag lives in memory, it always reads OFF again once the new
process finishes restarting; there's no separate "system is back up" event,
just the absence of "Error" being ON.


# Py-K93SYS self-update entity (always on)

One HA update entity, entity_id update.py_k93sys, diagnostic, always
registered (no config toggle to disable the entity itself). installed_version
is the running VERSION; latest_version comes from the same
local/web/both update check the script already runs at startup
(UPDATE_MODE), re-checked every device.self_update_check_interval seconds
(default 3600). Shows "update available" for either an upgrade or, per the
rollback support above, a version mismatch in either direction. The state is
published retained, so HA gets the current value immediately on subscribe
instead of waiting up to a full check interval for the next publish.

The Install button appears whenever device.self_update_allow_install is
true (default true - set to false to hide it). Pressing it reports
in_progress immediately, then re-checks for a candidate and, if one is
found, applies it and restarts the process exactly like the existing
headless auto-update path does (same download/extract/integrity-check/
rollback-on-failure machinery) - the whole process image is replaced, so the
MQTT connection drops and reconnects once the new version is up (the new
process's first state publish naturally clears in_progress).

  device:
    self_update_check_interval: 3600
    self_update_allow_install: true


# Terminal input queue

Commands sent to the "Terminal Input" text entity are FIFO-queued and run
one at a time; a command entered while another is still running is no
longer dropped. Queue depth is bounded by
terminal.terminal_input.input_queue_limit (default 25); when full the
oldest queued command is discarded and a note is written to Terminal
Output.


# Update source selection (K93SYS.py UPDATE_MODE constant):
#   "none"   -> disable updates entirely
#   "local"  -> only check RELEASES_DIR
#   "web"    -> only check the WEB_MANIFEST_URL manifest
#   "github" -> only check the GITHUB_REPO releases API
#   "both"   -> check local + web, pick the highest-version candidate
#   "all"    -> check local + web + github, pick the highest-version candidate
# Any candidate whose version differs from VERSION (not just newer) is
# offered, so pointing a manifest/release at an older version rolls a
# fleet back - see the rollback support note above.

# Manifest format example (for "web" / WEB_MANIFEST_URL):
# {
#   "latest": "0.0.79",
#   "download_url": "https://example.com/py-k93sys/releases/0.0.79.zip"
# }

# GitHub source (for "github"/"all" / GITHUB_REPO):
#   Set GITHUB_REPO = "owner/repo" in K93SYS.py. It calls
#   https://api.github.com/repos/<owner>/<repo>/releases/latest, reads the
#   release's tag_name as the version (a leading "v" is stripped), and
#   downloads the first release asset whose filename ends with
#   GITHUB_ASSET_SUFFIX (default ".tar.gz") - i.e. attach the exact
#   .tar.gz that make.ps1 already produces as a release asset when you cut
#   a GitHub release; K93SYS downloads and applies it exactly like a "web"
#   update. GitHub's own auto-generated "Source code" archives are not
#   used - they contain the whole repo (make.ps1, WEB/, etc.), not just
#   MASTER/'s contents, so they can't be applied directly.
