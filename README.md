# herdr-repetui

A [herdr](https://herdr.dev) plugin (`repetui.bar`) that shows the
[repetui](https://github.com/liyb-gz/repetui) Anki reviewer as a bar you toggle
with a key: along the bottom of the tab or down the side.

repetui keeps running in the background, in one `dtach` session. The bar is only
a viewer on it. Hiding the bar does not stop repetui, and you come back to the
same card.

macOS only.

## Requirements

- herdr 0.9.5 or newer
- `dtach` (`brew install dtach`)
- the forked repetui, branch `herdr-bar`, on `PATH` (or set `repetui` in the
  config to its absolute path):

  ```sh
  git clone -b herdr-bar https://github.com/liyb-gz/repetui.git
  uv tool install ./repetui      # or: pipx install ./repetui
  ```

- Python 3.11 or newer as `python3` on `PATH` (standard library only)

## Install

```sh
herdr plugin link /path/to/herdr-repetui
```

Add the key bindings to herdr's `config.toml` (usually
`~/.config/herdr/config.toml`), then run `herdr server reload-config`:

```toml
[[keys.command]]
key = "prefix+a"
type = "plugin_action"
command = "repetui.bar.toggle-bottom"
description = "Anki bottom bar"

[[keys.command]]
key = "prefix+shift+a"
type = "plugin_action"
command = "repetui.bar.toggle-side"
description = "Anki sidebar"
```

## Use

- `prefix+a` shows the bottom bar. Press it again to hide it.
- `prefix+shift+a` does the same for the sidebar. Pressing the other key while a
  bar is showing moves the bar to the other side.
- There is at most one bar at a time, across all tabs and herdr sessions.
  Toggling in another tab moves it there.
- Size: a quarter of the tab height for the bottom bar, a fifth of the tab width
  for the sidebar, with a minimum of 6 rows or 40 columns.

repetui starts on the first toggle. To quit it for real, press `q` in repetui, or
run the `quit` action (`repetui.bar.quit`, "Quit Anki review"), which shows the
bar where you are and then sends `q`. Whether quitting syncs first is repetui's
own **Sync on quit** setting, in its `?` screen, along with its other settings.

**Anki Desktop must stay closed while repetui runs.** They share one collection.
If Desktop gets it first, repetui shows a retry screen instead of the card.

If repetui is still running when herdr starts, for example after a herdr
restart, the plugin tells you ("repetui is running in the background"). The
notice is a herdr toast if toasts are on (`ui.toast.delivery` in herdr's config
is anything but `off`, which is herdr's default), and a macOS notification
otherwise. Errors from the toggle and quit actions are reported the same way.

## Config

Optional. The file is `config.toml` in the plugin's config directory; find it
with:

```sh
herdr plugin config-dir repetui.bar
```

Setting `HERDR_REPETUI_CONFIG` to a file path uses that file instead (it must
exist). A malformed file or an invalid value is reported as an error notice.

| Key | Default | Meaning |
|---|---|---|
| `repetui` | `"repetui"` | repetui command, or an absolute path |
| `dtach` | `"dtach"` | dtach command, or an absolute path |
| `socket` | `""` | dtach socket. Empty means `repetui.sock` in the plugin state directory |
| `anki_home` | `"~/Library/Application Support/Anki2"` | Anki data directory (passed to repetui) |
| `profile` | `""` | Anki profile. Empty leaves the choice to repetui |
| `bottom_fraction` | `0.25` | bottom bar share of the tab height (0 to 1, exclusive) |
| `side_fraction` | `0.2` | sidebar share of the tab width (0 to 1, exclusive) |
| `state_dir` | `""` | where state is kept. Empty means the plugin state directory |

`socket`, `anki_home` and `state_dir` must be absolute paths; `~` is expanded.
A fraction outside 0 to 1 falls back to its default. A Unix socket path must be
shorter than 104 bytes: the default socket moves to
`/private/tmp/herdr-repetui-<uid>/` if the state directory is too deep, but a
`socket` you set that is too long is an error.

## How it works

The bar pane runs `launch.py`, which starts repetui under `dtach` if no session
exists yet (one owner, guarded by a lock file next to the socket) and then
attaches to it. Toggling opens that pane in a temporary tab and moves it beside
the pane you are in at the right size, or closes it. Closing the pane ends only
the dtach client, never repetui. A small state file records the current bar so
the toggle can find it from any tab.
