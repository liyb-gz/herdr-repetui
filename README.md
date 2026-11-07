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
  for the sidebar. The minimum of 6 rows or 40 columns is best effort: in a small
  pane the bar can end up smaller.

repetui starts on the first toggle. To quit it for real, press `q` in repetui, or
run the `quit` action ("Quit Anki review"), which shows the bar where you are and
then sends `q`:

```sh
herdr plugin action invoke repetui.bar.quit
```

Whether quitting syncs first is repetui's own **Sync on quit** setting, in its `?`
screen, along with its other settings.

Optionally, bind `quit` to a key as well, with another `[[keys.command]]` entry.
The key here is only an example; pick one you like that herdr doesn't use:

```toml
[[keys.command]]
key = "prefix+alt+q"
type = "plugin_action"
command = "repetui.bar.quit"
description = "Quit Anki review"
```

**Anki Desktop must stay closed while repetui runs.** They share one collection.
If Desktop gets it first, repetui shows a retry screen instead of the card.

If repetui is still running when herdr starts, for example after a herdr
restart, the plugin tells you ("repetui is running in the background"). The
notice is a herdr toast only if `ui.toast.delivery` in herdr's config is
`herdr`, `terminal` or `system`. Any other value, or no setting at all (herdr's
default is `off`), counts as off. Otherwise, and also when the herdr call fails or
reports that nothing was shown, it is a macOS notification.

## Config

Optional. The file is `config.toml` in the plugin's config directory; find it
with:

```sh
herdr plugin config-dir repetui.bar
```

Setting `HERDR_REPETUI_CONFIG` to a file path uses that file instead (it must
exist).

| Key | Default | Meaning |
|---|---|---|
| `repetui` | `"repetui"` | repetui command, or an absolute path |
| `dtach` | `"dtach"` | dtach command, or an absolute path |
| `socket` | `""` | dtach socket. Empty means `repetui.sock` in the plugin state directory |
| `anki_home` | `"~/Library/Application Support/Anki2"` | Anki data directory (passed to repetui) |
| `profile` | `""` | Anki profile. Empty leaves the choice to repetui |
| `bottom_fraction` | `0.25` | bottom bar share of the tab height (0 to 1, exclusive) |
| `side_fraction` | `0.2` | sidebar share of the tab width (0 to 1, exclusive) |
| `span_tab` | `true` | The bar spans the whole tab, under (or beside) every pane. The other panes pass through a temporary `repetui-layout` tab for a moment, so the tab briefly flickers. Their processes and pane ids are kept, and hiding the bar restores the layout. `false` splits only the focused pane, with no flicker |
| `state_dir` | `""` | Directory for the default socket (and so its lock and state files). Ignored when `socket` is set. Empty means `$HERDR_PLUGIN_STATE_DIR` |

`socket`, `anki_home` and `state_dir` must be absolute paths; `~` is expanded. A
Unix socket path must be shorter than 104 bytes: the default socket moves to
`/private/tmp/herdr-repetui-<uid>/` if the state directory is too deep, but a
`socket` you set that is too long is an error.

### Errors

- A fraction that is not a number between 0 and 1 falls back to its default,
  silently.
- Any other bad config (a malformed file, a wrong type, a relative path, a
  missing `HERDR_REPETUI_CONFIG` file) is an error. When the bar opens, it is
  shown inside the bar ("press any key to close"), as is a missing `dtach` or
  `repetui` command. In the toggle and quit
  actions it appears as a notice, delivered as described above (herdr toast, or
  macOS notification).
- Other failures in those actions (herdr errors, lock timeouts) appear as a
  notice the same way.

## How it works

The bar pane runs `launch.py`, which starts repetui under `dtach` if no session
exists yet (one owner, guarded by a lock file next to the socket) and then
attaches to it. Toggling opens that pane in a temporary tab and moves it beside
the pane you are in at the right size, or closes it. Closing the pane ends only
the dtach client, never repetui. A small state file records the current bar so
the toggle can find it from any tab.
