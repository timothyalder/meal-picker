"""Add (or replace) the "Meals" view on the Overview dashboard and register the card.

The view is one custom:meals-card (card/meals-card.js, deployed by hand to
/config/www/meals/) in panel mode. Re-running replaces the view in place; other
views are left alone. A backup of the dashboard is written to backups/ first.
The card's Lovelace resource /local/meals/meals-card.js?v=N is created, or its
?v= updated, so phones pick up a new copy of the card.

Run from the home-assistant folder:
  .venv/bin/python shopping/build_meals_view.py --dry-run   # print the change, write nothing
  .venv/bin/python shopping/build_meals_view.py             # back up, then save

Note: dashboard/build_dashboard.py rebuilds the Overview as House + Energy only,
so re-run this script after running that one.
"""
import argparse
import asyncio
import datetime
import json
import os
import pathlib
import re

import websockets

ROOT = pathlib.Path(__file__).parent
URL = re.sub(r"^http", "ws", os.environ.get("HA_URL", "http://homeassistant.local:8123").rstrip("/")) + "/api/websocket"
TOKEN_FILE = pathlib.Path("~/.config/ha/token").expanduser()

CARD_VERSION = 5  # bump after copying a new meals-card.js to /config/www/meals/
RESOURCE = "/local/meals/meals-card.js"


def build_view():
    return {
        "title": "Meals",
        "path": "meals",
        "icon": "mdi:silverware-fork-knife",
        "type": "panel",
        "cards": [{"type": "custom:meals-card", "base": "/local/meals"}],
    }


def place_view(views, view):
    """views with `view` replacing the one at the same path, or appended. Returns (views, what happened)."""
    for i, v in enumerate(views):
        if v.get("path") == view["path"]:
            return views[:i] + [view] + views[i + 1:], f"replace view {i} ({v.get('title')!r})"
    return views + [view], f"append view {len(views)}"


async def main(dry_run, version):
    async with websockets.connect(URL, max_size=None) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": TOKEN_FILE.read_text().strip()}))
        assert json.loads(await ws.recv())["type"] == "auth_ok"
        n = 0

        async def call(**msg):
            nonlocal n
            n += 1
            await ws.send(json.dumps({"id": n, **msg}))
            while True:
                r = json.loads(await ws.recv())
                if r.get("id") == n and r.get("type") == "result":
                    if not r["success"]:
                        raise RuntimeError(f"{msg['type']}: {r['error']}")
                    return r["result"]

        # Dashboard view
        config = await call(type="lovelace/config", url_path=None)
        view = build_view()
        views, what = place_view(config["views"], view)
        print(f"view: {what}; views will be {[v.get('title') for v in views]}")
        print(json.dumps(view, indent=2))

        # Resource
        url = f"{RESOURCE}?v={version}"
        resources = await call(type="lovelace/resources")
        mine = [r for r in resources if r["url"].split("?")[0] == RESOURCE]
        if not mine:
            res_action = ("create", dict(type="lovelace/resources/create", res_type="module", url=url))
        elif mine[0]["url"] != url:
            res_action = ("update", dict(type="lovelace/resources/update", resource_id=mine[0]["id"],
                                         res_type="module", url=url))
        else:
            res_action = None
        if res_action:
            print(f"resource: {res_action[0]} {url}" + (f" (was {mine[0]['url']})" if mine else ""))
        else:
            print(f"resource: {url} already registered")

        if dry_run:
            print("dry run: nothing written")
            return

        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        (ROOT.parent / "backups" / f"overview-{stamp}.json").write_text(json.dumps(config, indent=2))
        config["views"] = views
        await call(type="lovelace/config/save", url_path=None, config=config)
        if res_action:
            await call(**res_action[1])
        print(f"saved: {len(views)} views, backup overview-{stamp}.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="print the change without saving anything")
    ap.add_argument("--version", type=int, default=CARD_VERSION, help="the ?v= for the card resource")
    args = ap.parse_args()
    asyncio.run(main(args.dry_run, args.version))
