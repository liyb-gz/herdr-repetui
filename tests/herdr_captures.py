"""Verbatim `herdr 0.9.5` CLI results captured through tools/e2e.sh (isolated session) on 2026-11-05.
The user name, host name and local paths are replaced with placeholders; nothing else is changed.

Source: docs/superpowers/notes/2026-11-05-spike-findings.md, section "CLI output shapes (Task 18)".
Each entry is the exact exit code, stdout and stderr of the command.
"""

CAPTURES = {
    'pane list': {
        "cmd": 'herdr pane list',
        "returncode": 0,
        "stdout": '{"id":"cli:pane:list","result":{"panes":[{"agent_status":"unknown","cwd":"/Users/user/projects/repetui-dev","focused":true,"foreground_cwd":"/Users/user/projects/repetui-dev","pane_id":"w1:p1","revision":1,"scroll":{"max_offset_from_bottom":0,"offset_from_bottom":0,"viewport_rows":33},"tab_id":"w1:t1","terminal_id":"term_65f840e5ca9321","terminal_title":"user@host:~/projects/repetui-dev","terminal_title_stripped":"user@host:~/projects/repetui-dev","workspace_id":"w1"},{"agent_status":"unknown","cwd":"/Users/user/projects/repetui-dev","focused":false,"foreground_cwd":"/Users/user/projects/repetui-dev","pane_id":"w1:p3","revision":1,"scroll":{"max_offset_from_bottom":0,"offset_from_bottom":0,"viewport_rows":33},"tab_id":"w1:t1","terminal_id":"term_65f840e5cb5922","terminal_title":"user@host:~/projects/repetui-dev","terminal_title_stripped":"user@host:~/projects/repetui-dev","workspace_id":"w1"},{"agent_status":"unknown","cwd":"/private/tmp/repetui-spike-plugin","focused":false,"pane_id":"w1:p4","restore_error":"Saved directory is unavailable. Restore the directory and restart this session.","revision":0,"tab_id":"w1:t1","terminal_id":"term_65f840e5cb5e23","workspace_id":"w1"},{"agent_status":"unknown","cwd":"/private/tmp","focused":false,"foreground_cwd":"/private/tmp","pane_id":"w2:p1","revision":1,"scroll":{"max_offset_from_bottom":0,"offset_from_bottom":0,"viewport_rows":47},"tab_id":"w2:t1","terminal_id":"term_65f840e5cc38d4","terminal_title":"user@host:/private/tmp","terminal_title_stripped":"user@host:/private/tmp","workspace_id":"w2"}],"type":"pane_list"}}\n',
        "stderr": '',
    },
    'plugin list (private)': {
        "cmd": 'herdr plugin list',
        "returncode": 0,
        "stdout": 'No plugins installed.\n',
        "stderr": '',
    },
    'pane layout': {
        "cmd": 'herdr pane layout --pane w1:p1',
        "returncode": 0,
        "stdout": '{"id":"cli:pane:layout","result":{"layout":{"area":{"height":47,"width":134,"x":0,"y":0},"focused_pane_id":"w1:p1","panes":[{"focused":true,"pane_id":"w1:p1","rect":{"height":35,"width":67,"x":0,"y":0}},{"focused":false,"pane_id":"w1:p3","rect":{"height":35,"width":67,"x":67,"y":0}},{"focused":false,"pane_id":"w1:p4","rect":{"height":12,"width":134,"x":0,"y":35}}],"splits":[{"direction":"down","id":"split_0_root","ratio":0.75,"rect":{"height":47,"width":134,"x":0,"y":0}},{"direction":"right","id":"split_1_0","ratio":0.5,"rect":{"height":35,"width":134,"x":0,"y":0}}],"tab_id":"w1:t1","workspace_id":"w1","zoomed":false},"type":"pane_layout"}}\n',
        "stderr": '',
    },
    'plugin link (private registry)': {
        "cmd": 'herdr plugin link /tmp/repetui-spike-plugin',
        "returncode": 0,
        "stdout": '{"id":"cli:plugin","result":{"plugin":{"enabled":true,"manifest_path":"/private/tmp/repetui-spike-plugin/herdr-plugin.toml","min_herdr_version":"0.9.5","name":"Probe","panes":[{"command":["sh","-c","exec sleep 600"],"id":"p","placement":"overlay","title":"Probe pane"}],"platforms":["macos"],"plugin_id":"spike.probe","plugin_root":"/private/tmp/repetui-spike-plugin","source":{"kind":"local"},"version":"0.0.1"},"type":"plugin_linked"}}\n',
        "stderr": '',
    },
    'pane split': {
        "cmd": 'herdr pane split w1:p1 --direction down --ratio 0.5 --no-focus',
        "returncode": 0,
        "stdout": '{"id":"cli:pane:split","result":{"pane":{"agent_status":"unknown","cwd":"/Users/user/projects/repetui-dev","focused":false,"foreground_cwd":"/Users/user/projects/repetui-dev","pane_id":"w1:p6","revision":0,"scroll":{"max_offset_from_bottom":0,"offset_from_bottom":0,"viewport_rows":15},"tab_id":"w1:t1","terminal_id":"term_65f840fb136105","workspace_id":"w1"},"type":"pane_info"}}\n',
        "stderr": '',
    },
    'plugin pane open': {
        "cmd": 'herdr plugin pane open --plugin spike.probe --entrypoint p --placement tab --workspace w1 --no-focus',
        "returncode": 0,
        "stdout": '{"id":"cli:plugin","result":{"plugin_pane":{"entrypoint":"p","pane":{"agent_status":"unknown","cwd":"/private/tmp/repetui-spike-plugin","focused":false,"foreground_cwd":"/private/tmp/repetui-spike-plugin","label":"Probe pane","pane_id":"w1:p7","revision":0,"scroll":{"max_offset_from_bottom":0,"offset_from_bottom":0,"viewport_rows":47},"tab_id":"w1:t3","terminal_id":"term_65f841001594a6","workspace_id":"w1"},"plugin_id":"spike.probe"},"type":"plugin_pane_opened"}}\n',
        "stderr": '',
    },
    'pane move (cross-tab, changed)': {
        "cmd": 'herdr pane move w1:p7 --tab w1:t1 --target-pane w1:p1 --split down --ratio 0.75 --focus',
        "returncode": 0,
        "stdout": '{"id":"cli:pane:move","result":{"move_result":{"changed":true,"closed_tab_id":"w1:t3","focused_pane_id":"w1:p7","pane":{"agent_status":"unknown","cwd":"/private/tmp/repetui-spike-plugin","focused":true,"foreground_cwd":"/private/tmp/repetui-spike-plugin","label":"Probe pane","pane_id":"w1:p7","revision":0,"scroll":{"max_offset_from_bottom":0,"offset_from_bottom":0,"viewport_rows":47},"tab_id":"w1:t1","terminal_id":"term_65f841001594a6","workspace_id":"w1"},"previous_pane_id":"w1:p7","previous_tab_id":"w1:t3","previous_workspace_id":"w1","target_layout":{"area":{"height":47,"width":134,"x":0,"y":0},"focused_pane_id":"w1:p7","panes":[{"focused":false,"pane_id":"w1:p1","rect":{"height":14,"width":67,"x":0,"y":0}},{"focused":true,"pane_id":"w1:p7","rect":{"height":4,"width":67,"x":0,"y":14}},{"focused":false,"pane_id":"w1:p6","rect":{"height":17,"width":67,"x":0,"y":18}},{"focused":false,"pane_id":"w1:p3","rect":{"height":35,"width":67,"x":67,"y":0}},{"focused":false,"pane_id":"w1:p4","rect":{"height":12,"width":134,"x":0,"y":35}}],"splits":[{"direction":"down","id":"split_0_root","ratio":0.75,"rect":{"height":47,"width":134,"x":0,"y":0}},{"direction":"right","id":"split_1_0","ratio":0.5,"rect":{"height":35,"width":134,"x":0,"y":0}},{"direction":"down","id":"split_2_00","ratio":0.5,"rect":{"height":35,"width":67,"x":0,"y":0}},{"direction":"down","id":"split_3_000","ratio":0.75,"rect":{"height":18,"width":67,"x":0,"y":0}}],"tab_id":"w1:t1","workspace_id":"w1","zoomed":false}},"type":"pane_move"}}\n',
        "stderr": '',
    },
    'pane move (same tab, unchanged)': {
        "cmd": 'herdr pane move w1:p7 --tab w1:t1 --target-pane w1:p1 --split right --ratio 0.8 --focus',
        "returncode": 0,
        "stdout": '{"id":"cli:pane:move","result":{"move_result":{"changed":false,"focused_pane_id":"w1:p7","pane":{"agent_status":"unknown","cwd":"/private/tmp/repetui-spike-plugin","focused":true,"foreground_cwd":"/private/tmp/repetui-spike-plugin","label":"Probe pane","pane_id":"w1:p7","revision":0,"scroll":{"max_offset_from_bottom":0,"offset_from_bottom":0,"viewport_rows":47},"tab_id":"w1:t1","terminal_id":"term_65f841001594a6","workspace_id":"w1"},"previous_pane_id":"w1:p7","previous_tab_id":"w1:t1","previous_workspace_id":"w1","reason":"same_tab","source_layout":{"area":{"height":47,"width":134,"x":0,"y":0},"focused_pane_id":"w1:p7","panes":[{"focused":false,"pane_id":"w1:p1","rect":{"height":14,"width":67,"x":0,"y":0}},{"focused":true,"pane_id":"w1:p7","rect":{"height":4,"width":67,"x":0,"y":14}},{"focused":false,"pane_id":"w1:p6","rect":{"height":17,"width":67,"x":0,"y":18}},{"focused":false,"pane_id":"w1:p3","rect":{"height":35,"width":67,"x":67,"y":0}},{"focused":false,"pane_id":"w1:p4","rect":{"height":12,"width":134,"x":0,"y":35}}],"splits":[{"direction":"down","id":"split_0_root","ratio":0.75,"rect":{"height":47,"width":134,"x":0,"y":0}},{"direction":"right","id":"split_1_0","ratio":0.5,"rect":{"height":35,"width":134,"x":0,"y":0}},{"direction":"down","id":"split_2_00","ratio":0.5,"rect":{"height":35,"width":67,"x":0,"y":0}},{"direction":"down","id":"split_3_000","ratio":0.75,"rect":{"height":18,"width":67,"x":0,"y":0}}],"tab_id":"w1:t1","workspace_id":"w1","zoomed":false},"target_layout":{"area":{"height":47,"width":134,"x":0,"y":0},"focused_pane_id":"w1:p7","panes":[{"focused":false,"pane_id":"w1:p1","rect":{"height":14,"width":67,"x":0,"y":0}},{"focused":true,"pane_id":"w1:p7","rect":{"height":4,"width":67,"x":0,"y":14}},{"focused":false,"pane_id":"w1:p6","rect":{"height":17,"width":67,"x":0,"y":18}},{"focused":false,"pane_id":"w1:p3","rect":{"height":35,"width":67,"x":67,"y":0}},{"focused":false,"pane_id":"w1:p4","rect":{"height":12,"width":134,"x":0,"y":35}}],"splits":[{"direction":"down","id":"split_0_root","ratio":0.75,"rect":{"height":47,"width":134,"x":0,"y":0}},{"direction":"right","id":"split_1_0","ratio":0.5,"rect":{"height":35,"width":134,"x":0,"y":0}},{"direction":"down","id":"split_2_00","ratio":0.5,"rect":{"height":35,"width":67,"x":0,"y":0}},{"direction":"down","id":"split_3_000","ratio":0.75,"rect":{"height":18,"width":67,"x":0,"y":0}}],"tab_id":"w1:t1","workspace_id":"w1","zoomed":false}},"type":"pane_move"}}\n',
        "stderr": '',
    },
    'pane send-keys (plain shell pane)': {
        "cmd": 'herdr pane send-keys w1:p6 q',
        "returncode": 0,
        "stdout": '',
        "stderr": '',
    },
    'pane send-keys (missing pane)': {
        "cmd": 'herdr pane send-keys w999:p999 q',
        "returncode": 1,
        "stdout": '',
        "stderr": '{"error":{"code":"pane_not_found","message":"pane w999:p999 not found"},"id":"cli:request"}\n',
    },
    'notification show': {
        "cmd": 'herdr notification show t --body b',
        "returncode": 0,
        "stdout": '{"id":"cli:notification:show","result":{"reason":"shown","shown":true,"type":"notification_show"}}\n',
        "stderr": '',
    },
    'pane get (missing: error case)': {
        "cmd": 'herdr pane get w999:p999',
        "returncode": 1,
        "stdout": '',
        "stderr": '{"error":{"code":"pane_not_found","message":"pane w999:p999 not found"},"id":"cli:pane:get"}\n',
    },
    'plugin pane close (plugin pane)': {
        "cmd": 'herdr plugin pane close w1:p7',
        "returncode": 0,
        "stdout": '{"id":"cli:plugin","result":{"pane_id":"w1:p7","type":"plugin_pane_closed"}}\n',
        "stderr": '',
    },
    'plugin pane close (again: already gone)': {
        "cmd": 'herdr plugin pane close w1:p7',
        "returncode": 1,
        "stdout": '',
        "stderr": '{"error":{"code":"plugin_pane_not_found","message":"plugin pane not found"},"id":"cli:plugin"}\n',
    },
    'plugin pane close (non-plugin pane)': {
        "cmd": 'herdr plugin pane close w1:p6',
        "returncode": 1,
        "stdout": '',
        "stderr": '{"error":{"code":"plugin_pane_not_found","message":"plugin pane not found"},"id":"cli:plugin"}\n',
    },
    'pane close (cleanup of the split pane)': {
        "cmd": 'herdr pane close w1:p6',
        "returncode": 0,
        "stdout": '{"id":"cli:pane:close","result":{"type":"ok"}}\n',
        "stderr": '',
    },
    'plugin unlink (cleanup)': {
        "cmd": 'herdr plugin unlink spike.probe',
        "returncode": 0,
        "stdout": '{"id":"cli:plugin","result":{"plugin_id":"spike.probe","removed":true,"type":"plugin_unlinked"}}\n',
        "stderr": '',
    },
    'plugin list after unlink': {
        "cmd": 'herdr plugin list',
        "returncode": 0,
        "stdout": 'No plugins installed.\n',
        "stderr": '',
    },
}
