from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sqlite3
from time import monotonic

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.events import Key, Resize
from textual.widgets import Static

from .data import GitmemTUISnapshot, PageName, build_tui_snapshot, current_search_results, filter_facts
from .render import RenderState, render_footer, render_nav, render_page, render_subnav, render_to_plain


@dataclass(slots=True)
class AppState:
    current_page: PageName = "overview"
    active_pane: dict[PageName, int] = field(
        default_factory=lambda: {
            "overview": 0,
            "facts": 1,
            "sessions": 0,
            "dream": 0,
            "prs": 0,
            "search": 1,
        }
    )
    fact_index: int = 0
    session_index: int = 0
    pr_index: int = 0
    search_index: int = 0
    facts_query: str = ""
    search_query: str = ""
    input_mode: str | None = None
    selected_conflict_option: str = "A"
    page_stack: list[PageName] = field(default_factory=list)
    notice: str | None = None


class GitmemTUIApp(App[None]):
    CSS = """
    Screen {
        layout: vertical;
        background: #0b0e14;
        color: #c9d1d9;
    }

    #nav, #subnav, #footer {
        height: auto;
    }

    #body {
        height: 1fr;
    }

    Static {
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("f2", "page_overview", "Overview"),
        Binding("f3", "page_facts", "Facts"),
        Binding("f4", "page_sessions", "Sessions"),
        Binding("f5", "page_dream", "Dream"),
        Binding("f6", "page_prs", "PRs"),
        Binding("f7", "page_search", "Search"),
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("left", "move_left", "Left", show=False),
        Binding("right", "move_right", "Right", show=False),
        Binding("enter", "activate", "Open", show=False),
        Binding("escape", "back", "Back", show=False),
        Binding("q", "quit", "Quit", show=False),
        Binding("d", "context_d", "Run Dream", show=False),
        Binding("c", "context_c", "Conflicts", show=False),
        Binding("r", "replay_session", "Replay", show=False),
        Binding("a", "pick_a", "Option A", show=False),
        Binding("b", "pick_b", "Option B", show=False),
        Binding("x", "reject_item", "Reject", show=False),
    ]

    def __init__(self, cwd: Path) -> None:
        super().__init__()
        self.cwd = cwd.resolve()
        self.state = AppState()
        self.snapshot: GitmemTUISnapshot | None = None
        self._last_refresh_started = 0.0
        self._last_governance_refresh = 0.0
        self._governance_cache: dict | None = None

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(id="nav")
            yield Static(id="subnav")
            yield Static(id="body")
            yield Static(id="footer")

    def on_mount(self) -> None:
        self.refresh_snapshot(force=True)
        self.set_interval(0.5, self._tick)

    def on_resize(self, _: Resize) -> None:
        self.refresh_ui()

    def on_key(self, event: Key) -> None:
        if self.state.input_mode is None and event.key in {
            "f2",
            "f3",
            "f4",
            "f5",
            "f6",
            "f7",
        }:
            page = {
                "f2": "overview",
                "f3": "facts",
                "f4": "sessions",
                "f5": "dream",
                "f6": "prs",
                "f7": "search",
            }[event.key]
            self._set_page(page)
            event.stop()
            return
        if self.state.input_mode is None and event.character == "/":
            self.action_focus_filter()
            event.stop()
            return
        if self.state.input_mode is None:
            return
        if event.key in {"enter", "escape"}:
            if event.key == "escape":
                self._active_query_value(set_to="")
            self.state.input_mode = None
            self.state.notice = None
            self.refresh_ui()
            event.stop()
            return
        if event.key == "backspace":
            self._active_query_value(set_to=self._active_query_value()[:-1])
            self._reset_selection_for_query()
            self.refresh_ui()
            event.stop()
            return
        if event.character and event.character.isprintable():
            self._active_query_value(set_to=self._active_query_value() + event.character)
            self._reset_selection_for_query()
            self.refresh_ui()
            event.stop()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if self.state.input_mode is not None:
            return False
        return True

    def render_state(self) -> RenderState:
        return RenderState(
            current_page=self.state.current_page,
            active_pane=self.state.active_pane[self.state.current_page],
            fact_index=self.state.fact_index,
            session_index=self.state.session_index,
            pr_index=self.state.pr_index,
            search_index=self.state.search_index,
            facts_query=self.state.facts_query,
            search_query=self.state.search_query,
            input_mode=self.state.input_mode,
            selected_conflict_option=self.state.selected_conflict_option,
            notice=self.state.notice,
        )

    def render_body_plain(self, *, width: int = 120) -> str:
        if self.snapshot is None:
            return ""
        return render_to_plain(render_page(self.snapshot, self.render_state(), width=width), width=width)

    def render_nav_plain(self, *, width: int = 120) -> str:
        if self.snapshot is None:
            return ""
        return render_to_plain(render_nav(self.snapshot, self.render_state(), width=width), width=width)

    def refresh_snapshot(self, *, force: bool = False) -> None:
        now = monotonic()
        if not force and now - self._last_refresh_started < 2.0:
            self.refresh_ui()
            return
        self._last_refresh_started = now
        governance_payload = self._governance_cache
        if force or governance_payload is None or now - self._last_governance_refresh >= 15.0:
            governance_payload = None
            self._last_governance_refresh = now
        try:
            snapshot = build_tui_snapshot(self.cwd, governance_payload=governance_payload)
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
            if self.snapshot is None:
                raise
            self.state.notice = f"refresh failed: {exc}"
            self.refresh_ui()
            return
        self.snapshot = snapshot
        self._governance_cache = self.snapshot.governance_payload
        if self.state.notice and self.state.notice.startswith("refresh failed:"):
            self.state.notice = None
        self._clamp_selection()
        self.refresh_ui()

    def refresh_ui(self) -> None:
        if self.snapshot is None or not self.is_mounted:
            return
        width = max(80, self.size.width - 2)
        state = self.render_state()
        self.query_one("#nav", Static).update(render_nav(self.snapshot, state, width=width))
        self.query_one("#subnav", Static).update(render_subnav(self.snapshot, state, width=width))
        self.query_one("#body", Static).update(render_page(self.snapshot, state, width=width))
        self.query_one("#footer", Static).update(render_footer(self.snapshot, state, width=width))

    def _tick(self) -> None:
        self.refresh_snapshot()

    def _clamp_selection(self) -> None:
        if self.snapshot is None:
            return
        facts = filter_facts(self.snapshot, self.state.facts_query)
        searches = current_search_results(self.snapshot, self.state.search_query)
        self.state.fact_index = self._clamp(self.state.fact_index, len(facts))
        self.state.session_index = self._clamp(self.state.session_index, len(self.snapshot.sessions))
        self.state.pr_index = self._clamp(self.state.pr_index, len(self.snapshot.governance_payload.get("open_prs") or []))
        self.state.search_index = self._clamp(self.state.search_index, len(searches))

    @staticmethod
    def _clamp(index: int, length: int) -> int:
        if length <= 0:
            return 0
        return max(0, min(index, length - 1))

    def _set_page(self, page: PageName) -> None:
        if page == self.state.current_page:
            return
        self.state.page_stack.append(self.state.current_page)
        self.state.current_page = page
        self.state.notice = None
        self._clamp_selection()
        self.refresh_ui()

    def action_page_overview(self) -> None:
        self._set_page("overview")

    def action_page_facts(self) -> None:
        self._set_page("facts")

    def action_page_sessions(self) -> None:
        self._set_page("sessions")

    def action_page_dream(self) -> None:
        self._set_page("dream")

    def action_page_prs(self) -> None:
        self._set_page("prs")

    def action_page_search(self) -> None:
        self._set_page("search")

    def action_move_up(self) -> None:
        if self.snapshot is None or self.state.input_mode is not None:
            return
        if self.state.current_page == "facts":
            self.state.fact_index = max(0, self.state.fact_index - 1)
        elif self.state.current_page == "sessions":
            self.state.session_index = max(0, self.state.session_index - 1)
        elif self.state.current_page == "prs":
            self.state.pr_index = max(0, self.state.pr_index - 1)
        elif self.state.current_page == "search":
            self.state.search_index = max(0, self.state.search_index - 1)
        self.refresh_ui()

    def action_move_down(self) -> None:
        if self.snapshot is None or self.state.input_mode is not None:
            return
        if self.state.current_page == "facts":
            self.state.fact_index += 1
        elif self.state.current_page == "sessions":
            self.state.session_index += 1
        elif self.state.current_page == "prs":
            self.state.pr_index += 1
        elif self.state.current_page == "search":
            self.state.search_index += 1
        self._clamp_selection()
        self.refresh_ui()

    def action_move_left(self) -> None:
        pane = self.state.active_pane[self.state.current_page]
        self.state.active_pane[self.state.current_page] = max(0, pane - 1)
        self.refresh_ui()

    def action_move_right(self) -> None:
        max_pane = {"overview": 3, "facts": 2, "sessions": 2, "dream": 1, "prs": 1, "search": 2}[self.state.current_page]
        pane = self.state.active_pane[self.state.current_page]
        self.state.active_pane[self.state.current_page] = min(max_pane, pane + 1)
        self.refresh_ui()

    def action_focus_filter(self) -> None:
        self.state.input_mode = "search" if self.state.current_page == "search" else "facts"
        self.state.notice = "type your query and press Enter"
        self.refresh_ui()

    def action_back(self) -> None:
        if self.state.input_mode is not None:
            self.state.input_mode = None
            self.state.notice = None
            self.refresh_ui()
            return
        if self.state.page_stack:
            self.state.current_page = self.state.page_stack.pop()
            self.state.notice = None
            self.refresh_ui()

    def action_activate(self) -> None:
        if self.state.current_page == "prs":
            self.state.notice = "manual merge choices are preview-only in this first TUI cut"
        elif self.state.current_page == "sessions":
            self.state.notice = "session detail stays synced to the selected row"
        elif self.state.current_page == "facts":
            self.state.notice = "fact detail stays synced to the selected row"
        self.refresh_ui()

    def action_run_dream(self) -> None:
        self.state.notice = f"run Dream from the CLI with: gitmem dream --cwd {self.cwd}"
        self.refresh_ui()

    def action_context_c(self) -> None:
        if self.state.current_page == "prs":
            self.action_pick_option("C")
            return
        self._set_page("prs")
        self.state.notice = "showing PR/conflict view"
        self.refresh_ui()

    def action_context_d(self) -> None:
        if self.state.current_page == "prs":
            self.action_pick_option("D")
            return
        self.action_run_dream()

    def action_replay_session(self) -> None:
        self._set_page("sessions")
        self.state.notice = "session transcript is live in the center pane"
        self.refresh_ui()

    def action_pick_option(self, option: str) -> None:
        self.state.selected_conflict_option = option
        self.state.notice = f"selected conflict option {option}"
        self.refresh_ui()

    def action_reject_item(self) -> None:
        self.state.notice = "PR rejection is not wired yet"
        self.refresh_ui()

    def action_pick_a(self) -> None:
        self.action_pick_option("A")

    def action_pick_b(self) -> None:
        self.action_pick_option("B")

    def _active_query_value(self, *, set_to: str | None = None) -> str:
        if self.state.input_mode == "search":
            if set_to is not None:
                self.state.search_query = set_to
            return self.state.search_query
        if set_to is not None:
            self.state.facts_query = set_to
        return self.state.facts_query

    def _reset_selection_for_query(self) -> None:
        if self.state.input_mode == "search":
            self.state.search_index = 0
        else:
            self.state.fact_index = 0
