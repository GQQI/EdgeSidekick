"""BrowserSandbox observe / index / extract methods."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


class BrowserSandboxPageMixin:
    def list_interactive_elements(
        self, *, user_id: Optional[str] = None, limit: int = 60
    ) -> list[dict[str, Any]]:
        uid = self._lane_uid(user_id)
        n = max(1, min(int(limit or 60), 150))

        async def _op() -> list[dict[str, Any]]:
            with self._lock:
                page = await self._alive_page(uid)
            js = _LIST_ELEMENTS_JS.replace("__LIMIT__", str(n))
            raw = await page.evaluate(js)
            return list(raw or [])

        return _WORKER.call(_op, timeout=30.0)

    def save_screenshot_to_workspace(
        self,
        workspace: Path,
        *,
        user_id: Optional[str] = None,
        name: str = "",
        full_page: bool = False,
    ) -> Path:
        png = self.screenshot_png(user_id=user_id, full_page=full_page)
        out_dir = Path(workspace) / ".sidekick" / "browser"
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = (name or f"shot_{int(time.time())}.png").replace("..", "")
        if not fname.endswith(".png"):
            fname += ".png"
        path = out_dir / fname
        path.write_bytes(png)
        page_url = self.last_url(user_id)
        meta = {
            "url": page_url,
            "path": fname,
            "saved_at": time.time(),
        }
        try:
            path.with_name(path.name + ".json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (out_dir / "latest.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
        return path

    async def _observe_on_page(
        self,
        page: Any,
        sess: dict[str, Any],
        *,
        limit: int = 100,
        expand: int = 400,
        highlight_all: bool = False,
    ) -> dict[str, Any]:
        """Stamp data-bk-idx on interactive nodes; return structured observation."""
        n = max(1, min(int(limit or 100), 200))
        exp = max(-1, min(int(expand if expand is not None else 400), 5000))
        js = _bobs.observe_js(limit=n, expand=exp, highlight_all=bool(highlight_all))
        prev_sig: Any = None
        raw: Any = None
        for _ in range(3):
            raw = await page.evaluate(js)
            els = (raw or {}).get("elements") or []
            sig = tuple(
                (e.get("index"), e.get("tag"), e.get("name"), e.get("href"))
                for e in els[:24]
                if isinstance(e, dict)
            )
            if sig == prev_sig:
                break
            prev_sig = sig
            await page.wait_for_timeout(350)
        data = dict(raw or {})
        elements = [e for e in (data.get("elements") or []) if isinstance(e, dict)]
        try:
            cur_url = str(getattr(page, "url", "") or data.get("url") or "")
        except Exception:
            cur_url = str(data.get("url") or "")
        refs: dict[str, str] = {}
        index_map: dict[int, str] = {}
        for e in elements:
            idx = e.get("index")
            if idx is None:
                continue
            sel = f'[{_bobs.IDX_ATTR}="{idx}"]'
            refs[f"e{idx}"] = sel
            refs[str(idx)] = sel
            try:
                index_map[int(idx)] = sel
            except Exception:
                pass
        sess["refs"] = refs
        sess["index_map"] = index_map
        if cur_url:
            sess["url"] = cur_url
        data["url"] = cur_url or data.get("url")
        data["elements"] = elements
        data["count"] = len(elements)
        try:
            challenge = await self._probe_challenge(page)
        except Exception:
            challenge = {"challenged": False}
        if challenge.get("challenged"):
            data["needs_human"] = True
            data["challenge"] = challenge
        tabs_info: list[dict[str, Any]] = []
        try:
            cur = sess.get("page")
            for i, p in enumerate(self._context_pages(sess)):
                try:
                    title = (await p.title()) or ""
                except Exception:
                    title = ""
                try:
                    u = str(getattr(p, "url", "") or "")
                except Exception:
                    u = ""
                tabs_info.append(
                    {"index": i, "url": u, "title": title[:80], "current": p is cur}
                )
        except Exception:
            tabs_info = []
        data["tabs"] = tabs_info
        data["hint"] = (
            "Act with browser_click(index=N) / browser_input(index=N, text=...). "
            "If the target is missing, browser_scroll or browser_scroll_to_text. "
            "Never navigate back to the site homepage to restart. "
            "On needs_human, call browser_wait_for_user."
        )
        return data

    def observe_page(
        self,
        *,
        user_id: Optional[str] = None,
        limit: int = 40,
        expand: int = 0,
        highlight_all: bool = False,
    ) -> dict[str, Any]:
        """Index viewport interactive elements (capped + ranked); compact text."""
        uid = self._lane_uid(user_id)
        n = max(1, min(int(limit or 40), 80))
        # expand=0 => strict viewport (browser-use / Playwright-MCP viewport-first).
        exp = max(-1, min(int(expand if expand is not None else 0), 5000))

        async def _op() -> dict[str, Any]:
            with self._lock:
                page = await self._alive_page(uid)
                sess = self._sessions[uid]
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            data = await self._observe_on_page(
                page, sess, limit=n, expand=exp, highlight_all=bool(highlight_all)
            )
            try:
                cur_url = str(data.get("url") or "")
            except Exception:
                cur_url = ""
            with self._lock:
                if cur_url:
                    sess["url"] = cur_url
                    self._last_url[uid] = cur_url
            # Dual shape: new observation text + legacy elements with ref=eN
            packed = _bobs.observation_payload(
                data, tabs=data.get("tabs"), action_note="observe"
            )
            elements = []
            for e in packed.get("elements") or []:
                if not isinstance(e, dict):
                    continue
                item = {
                    "ref": f"e{e.get('index')}",
                    "index": e.get("index"),
                    "tag": e.get("tag"),
                    "role": e.get("role"),
                    "name": e.get("name"),
                }
                for k in ("href", "placeholder", "value", "type", "home_link", "checked"):
                    if k in e:
                        item[k] = e[k]
                elements.append(item)
            packed["elements"] = elements
            packed["count"] = len(elements)
            return packed

        return _WORKER.call(_op, timeout=45.0)

    async def _highlight_index(self, page: Any, index: int) -> None:
        try:
            await page.evaluate(_bobs.highlight_index_js(), int(index))
            await page.wait_for_timeout(450)
        except Exception:
            pass

    async def _clear_highlight(self, page: Any) -> None:
        try:
            await page.evaluate(_bobs.clear_highlight_js())
        except Exception:
            pass

    def _index_selector(self, sess: dict[str, Any], index: int) -> str:
        amap = sess.get("index_map") or {}
        if int(index) in amap:
            return str(amap[int(index)])
        return f'[{_bobs.IDX_ATTR}="{int(index)}"]'

    def _agent_hold(self, uid: str, index: int | None = None) -> dict[str, Any] | None:
        """Refuse agent clicks while a person must finish, or the same click is stuck."""
        from .browser_queue import human_hold_result, repeated_click_result, skip_repeated_click

        with self._lock:
            sess = self._sessions.get(uid)
            if not sess:
                return None
            url = str(sess.get("url") or "")
            if sess.get("needs_human"):
                return human_hold_result(url)
            if index is not None and skip_repeated_click(sess.get("last_click"), index):
                return repeated_click_result(url, index)
        return None

    def click_index(self, index: int, *, user_id: Optional[str] = None) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        held = self._agent_hold(uid, int(index))
        if held:
            return held

        async def _do(page: Any, sess: dict[str, Any]) -> dict[str, Any]:
            sel = self._index_selector(sess, int(index))
            await self._highlight_index(page, int(index))
            try:
                await self._click_on(page, sel, sess=sess)
            finally:
                await self._clear_highlight(page)
            return {"action": "click", "index": int(index), "selector": sel}

        return self._run_action(uid, _do, observe_after=True)

    def input_index(
        self,
        index: int,
        text: str,
        *,
        submit: bool = False,
        clear: bool = True,
        user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        held = self._agent_hold(uid)
        if held:
            return held

        async def _do(page: Any, sess: dict[str, Any]) -> dict[str, Any]:
            sel = self._index_selector(sess, int(index))
            await self._highlight_index(page, int(index))
            try:
                loc = page.locator(sel).first
                if clear:
                    try:
                        await loc.fill("")
                    except Exception:
                        pass
                await loc.fill(str(text), timeout=15000)
                if submit:
                    try:
                        await loc.press("Enter")
                    except Exception:
                        await page.keyboard.press("Enter")
            finally:
                await self._clear_highlight(page)
            return {
                "action": "input",
                "index": int(index),
                "text": str(text),
                "submit": bool(submit),
            }

        return self._run_action(uid, _do, observe_after=True)

    def scroll_to_text(self, text: str, *, user_id: Optional[str] = None) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        needle = (text or "").strip()
        if not needle:
            raise ValueError("empty text")

        async def _do(page: Any, sess: dict[str, Any]) -> dict[str, Any]:
            raw = await page.evaluate(_bobs.scroll_to_text_js(), needle)
            return {"action": "scroll_to_text", "text": needle, **dict(raw or {})}

        return self._run_action(uid, _do, observe_after=True)

    def extract_content(
        self,
        *,
        user_id: Optional[str] = None,
        goal: str = "",
        max_chars: int = 12000,
    ) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        limit = max(500, min(int(max_chars or 12000), 50000))

        async def _op() -> dict[str, Any]:
            with self._lock:
                page = await self._alive_page(uid)
            raw = await page.evaluate(_bobs.extract_content_js(), limit)
            out = dict(raw or {})
            if goal:
                out["goal"] = goal
            if goal and out.get("text"):
                tokens = [t for t in re.split(r"\s+", goal) if len(t) >= 2][:8]
                body = str(out.get("text") or "")
                if tokens:
                    hits = [
                        line
                        for line in re.split(r"(?<=[。！？.!?\n])", body)
                        if any(tok.lower() in line.lower() for tok in tokens)
                    ]
                    if hits:
                        clipped = "\n".join(hits)
                        if len(clipped) > limit:
                            clipped = clipped[:limit]
                        out["text"] = clipped
                        out["filtered_by_goal"] = True
            return out

        return _WORKER.call(_op, timeout=30.0)

    def send_keys(
        self, keys: str, *, user_id: Optional[str] = None, index: Optional[int] = None
    ) -> dict[str, Any]:
        uid = self._lane_uid(user_id)
        key = (keys or "").strip()
        if not key:
            raise ValueError("empty keys")

        async def _do(page: Any, sess: dict[str, Any]) -> dict[str, Any]:
            if index is not None:
                sel = self._index_selector(sess, int(index))
                await self._highlight_index(page, int(index))
                try:
                    await page.locator(sel).first.focus(timeout=8000)
                finally:
                    await self._clear_highlight(page)
            await page.keyboard.press(key)
            return {"action": "send_keys", "keys": key, "index": index}

        return self._run_action(uid, _do, observe_after=True)



