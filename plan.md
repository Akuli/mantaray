## Plan: Refactor Mantaray for backend/frontend state boundary

TL;DR - introduce a minimal application state model for views, messages, and notifications; move event handling to state updates; make GUI render from state; and define a clean user input boundary.

**Steps**
1. Add a dedicated state module.
   - Create `mantaray/state.py` with dataclasses for AppState, ServerState, ViewState, MessageState, and user list / notification metadata.
   - Keep state minimal: view identity, type, name, parent relationship, message list, notification state, user lists, and connection metadata.
   - Define `UserAction` / `ClientCommand` classes for future frontend/backend input commands.

2. Refactor backend event handling to update state rather than widgets.
   - Move backend networking and protocol types into `mantaray/backend/`, with `IrcEvent` and `IrcCore` defined in `mantaray/backend/core.py`.
   - Change `mantaray/backend/received.py` so it no longer imports or manipulates `views.View` objects.
   - Add state-update helpers such as `add_privmsg_to_view_state`, `handle_join_in_state`, `handle_part_in_state`, `handle_nick_change_in_state`, `handle_cap_in_state`, `handle_connectivity_in_state`, etc.
   - Ensure `IrcCore` remains GUI-agnostic and emits `IrcEvent`; a new layer translates those into state changes.

3. Introduce explicit state synchronization in the frontend.
   - Keep backend networking and event emission separate from GUI widgets.
   - Implement `sync_from_state()` methods on frontend view widgets that take the corresponding `ViewState` or `ServerState` and update themselves.
   - The backend loop should call `core.run_one_step()`, collect events, and translate them into state mutations via `mantaray/backend/received.py`.
   - Keep reconnect / SASL / nickmask logic in `IrcCore` and event translation in the backend layer.

4. Refactor the frontend to render state instead of driving state.
   - Organize GUI code under `mantaray/frontend/` where practical.
   - Make frontend code own `AppState` or at least map its visual widgets to state objects.
   - Change `IrcWidget` to build tree entries and text widgets from state events, not from direct backend/received logic.
   - Keep widget classes in `mantaray/frontend/views.py`, but move text insertion / highlight application into renderer methods that sync against `MessageState` lists.
   - The frontend should compare state with current widgets and create/destroy views or append missing messages.

5. Cleanly separate input handling.
   - Change `IrcWidget.on_enter_pressed` and `commands.handle_command` to consume view/state identifiers and return or dispatch `UserAction` objects instead of operating on widget methods directly.
   - Define a small input interface so the front end can later send actions across a network boundary.
   - For now the backend can handle these actions immediately by using the functional state layer.

6. Preserve existing behavior while moving boundaries.
   - Keep `ServerSettings` and `config` as the configuration source.
   - Move all logging responsibilities to the backend; the frontend should not know about logs or log file paths.
   - Keep `commands.py` logic intact but refactor to use state and frontend sync methods.

**Relevant files**
- `mantaray/state.py` — shared state model.
- `mantaray/input.py` — input/action definitions.
- `mantaray/backend/core.py` — backend networking and IRC protocol core, including `IrcEvent` and `IrcCore`.
- `mantaray/backend/received.py` — backend event translation into state changes.
- `mantaray/frontend/gui.py` — GUI rendering and state observation, including state synchronization coordination.
- `mantaray/frontend/views.py` — widget renderer classes with `sync_from_state()` methods.
- `mantaray/commands.py` — input command handling through the new state/action boundary.
- `tests/` — preserve and fix existing tests with minimal changes; avoid adding new coverage unless required to keep behavior intact.

**Verification**
1. Run the existing test suite and fix failures caused by the refactor, keeping test changes minimal and preserving intent.
2. Use existing GUI tests as the primary safety net for message arrival, view creation, nick changes, and notifications.
3. Verify the state model by keeping current test expectations intact while refactoring internals.
4. Manually run `python -m mantaray` after the refactor and confirm the client still connects locally, shows channels/PMs, and updates notifications.

**Decisions**
- The main abstraction boundary is `AppState` / `ServerState` / `ViewState`, not the raw widget tree.
- Message text is stored as immutable state entries; UI rendering is append-only and uses state as the source of truth.
- Input is represented by explicit action objects so it can later be serialized or sent across the network.

**Further Considerations**
1. We may want to keep `ViewState` lightweight by storing only a raw text line plus a list of derived tags, instead of full `MessagePart` objects.
2. The view renderer should avoid full re-rendering of text widgets; it should append new messages and only refresh selection/highlight state.
3. Because `ServerView` currently owns `IrcCore`, the first refactor step should preserve that ownership while moving UI update logic into state translation.

**Implementation design**
1. `mantaray/state.py`
   - `MessagePartState` and `MessageState` dataclasses for text+pairs of tags
   - `ViewState` with `view_id`, `view_type`, `name`, `parent_id`, list of `messages`, `notification_count`, selector tags, and channel/PM metadata
   - `ServerState` with server name, list of subview ids, joined channels, userlist data, away status, and last known connection name/nick
   - `AppState` with ordered server ids and mapping of states
   - `UserAction` classes for send message, execute command, change nick, select view, reconnect, and later remote input

2. `mantaray/backend/received.py`
   - split off state mutation code from current widget manipulation
   - add functions like `handle_event(server_state, event)` and helpers for PRIVMSG/join/part/nick/quit/capabilities
   - never import from `views` or any frontend/GUI modules; backend must not depend on the frontend
   - keep reconnect/SASL/nickmask behavior inside `IrcCore`


4. `mantaray/frontend/views.py`
   - preserve widget classes, but add `sync_from_state(view_state)` methods
   - add `create_view_from_state(irc_widget, view_state)` factory helpers
   - move `add_message` semantics to a low-level widget renderer used by the sync methods
   - keep existing `View.add_notification` and `mark_seen` behavior, but source notification state from `ViewState`
   - preserve the old `View.textwidget` API for internal use by logs and other modules

5. `mantaray/frontend/gui.py`
   - `IrcWidget` initializes state and coordinates backend state updates with frontend rendering
   - GUI selection and treeview state is driven from `AppState`, not directly by `View` objects
   - `on_enter_pressed()` constructs `UserAction` and dispatches to the backend/state update layer
   - add a periodic timer or state-change callback to sync widgets after backend events
   - retain focus-management and settings UI

6. `mantaray/commands.py`
   - refactor `handle_command()` to operate on view identifiers and `AppState` / backend state update functions instead of widget objects
   - preserve command behavior and error reporting as state mutations or UI renderings
   - keep permission/error messages in the current view rendered from state

7. `mantaray/backend/logs.py`
   - move log file management entirely into the backend package
   - preserve log file open/close semantics
   - adapt `read_old_logs()` to return message tuples that the frontend may render, but not to write or open logs itself
   - ensure `start_logging()` and `stop_logging()` are backend-only operations and do not require frontend objects

**Risks and mitigations**
- Current code mixes widget creation and state changes heavily; mitigate by keeping widget classes intact and adding state sync methods incrementally.
- Existing tests depend on widget objects; preserve these APIs while moving state logic behind them.
- Log reloading can still be widget-driven for now, with the new state boundary only used for live backend events.

**Next step**
- Apply the refactor incrementally, starting with `mantaray/state.py` and `mantaray/backend/received.py`, then frontend/view sync.
