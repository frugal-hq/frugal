# Frugal: review

Three modes; pick by the request.

**Diff review** (PR, branch, staged changes, named files). Read the diff and the code it calls; never review a line you have not traced. Two kinds of finding, bugs first:
- Bug: wrong result, crash, data loss, security hole, broken edge case. Needs a concrete input that fails.
- Excess: reinvented stdlib or native feature, unneeded dependency, abstraction with one user, dead option, code the task did not need. Needs the replacement.

One line each, most severe first: `path:line: <problem>. Fix: <change>.` No praise, no summary, no style nits a formatter would catch. Nothing found: say so in one line.

**Audit** (whole repo or a directory, for over-engineering). Grep and list before reading; read only the candidates. Return a ranked list, biggest deletion first: `path:line: <what to delete or replace> -> <replacement>, ~<lines saved>`. At most 15 items; say how many more exist. Edit nothing unless asked.

**Shortcut list** (debt ledger). Grep `frugal:` and `ponytail:` comments plus TODO, FIXME, HACK. One line each: `path:line: <shortcut>; upgrade when <condition>`. Group by file; flag any whose condition already holds.
