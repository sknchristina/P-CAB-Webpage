#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Notion에서 두 종류의 소스를 가져와 index.html을 갱신한다.

1) 데이터베이스("위산분비질환 문헌 업데이트")
   - Evidence Categories 막대그래프
   - Latest P-CAB Evidence 갤러리

2) 메인 페이지("P-CAB 현황 및 임상적 시사점") 본문 블록
   - Hero 소개 문구
   - Executive Snapshot (3개 카드)
   - P-CAB Evidence Map (표)
   - Clinical Implications (3개 카드)
   - Strategic Watchlist (체크리스트)
   - Bottom Line

환경변수:
  NOTION_TOKEN   - Notion Integration access token (필수)
  DATABASE_ID    - Notion 데이터베이스 ID (필수)
  PAGE_ID        - Notion 메인 페이지 ID (선택. 없으면 1)만 수행)
  INDEX_HTML     - 수정할 html 경로 (기본값 index.html)

주의:
  PAGE_ID를 사용하려면, 메인 페이지에도 Integration("PCAB landscape Sync")을
  공유(연결)해줘야 한다. 데이터베이스에만 연결되어 있으면 페이지 본문은
  읽을 수 없다(404).
"""

import os
import re
import sys
import json
import time
import html as htmlmod
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "").strip()
DATABASE_ID = os.environ.get("DATABASE_ID", "").strip()
PAGE_ID = os.environ.get("PAGE_ID", "").strip()
INDEX_HTML = os.environ.get("INDEX_HTML", "index.html")

NOTION_VERSION = "2022-06-28"
API_BASE = "https://api.notion.com/v1"

# 사이트에 표시할 카테고리 순서(현재 index.html과 동일한 순서 유지)
CATEGORY_ORDER = [
    "Helicobacter pylori", "동반질환", "GERD", "PPI",
    "P-CAB", "위염", "위궤양(GU)", "ERD",
]

# 자료유형(select) → 사이트 표기값 매핑 (필요시 수정)
TYPE_MAP = {
    "임상논문": "임상논문",
    "임상시험": "임상시험",
    "진료지침": "진료지침",
}


def die(msg):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def notion_request(path, payload=None):
    url = f"{API_BASE}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method="POST" if payload is not None else "GET",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        die(f"Notion API 오류 {e.code}: {body}")


def notion_get(path):
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": NOTION_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        die(f"Notion API 오류 {e.code}: {body}")


def fetch_all_pages(database_id):
    results = []
    cursor = None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        data = notion_request(f"/databases/{database_id}/query", payload)
        results.extend(data.get("results", []))
        if data.get("has_more"):
            cursor = data.get("next_cursor")
            time.sleep(0.3)  # rate limit 완화
        else:
            break
    return results


# ---------- Notion property 값 추출 헬퍼 ----------

def prop_text(props, name):
    """title / rich_text 계열에서 순수 텍스트 추출"""
    p = props.get(name)
    if not p:
        return ""
    t = p.get("type")
    if t == "title":
        return "".join(x.get("plain_text", "") for x in p.get("title", []))
    if t == "rich_text":
        return "".join(x.get("plain_text", "") for x in p.get("rich_text", []))
    if t == "url":
        return p.get("url") or ""
    if t == "select":
        sel = p.get("select")
        return sel.get("name", "") if sel else ""
    return ""


def prop_multiselect(props, name):
    p = props.get(name)
    if not p or p.get("type") != "multi_select":
        return []
    return [x.get("name", "") for x in p.get("multi_select", [])]


def prop_select(props, name):
    p = props.get(name)
    if not p or p.get("type") != "select":
        return ""
    sel = p.get("select")
    return sel.get("name", "") if sel else ""


def prop_date(props, name):
    p = props.get(name)
    if not p or p.get("type") != "date":
        return ""
    d = p.get("date")
    return (d.get("start") or "") if d else ""


def prop_url(props, name):
    p = props.get(name)
    if not p:
        return ""
    if p.get("type") == "url":
        return p.get("url") or ""
    return prop_text(props, name)


# ---------- 메인 변환 로직 ----------

def js_str(s):
    """JS 문자열 리터럴로 안전하게 이스케이프"""
    return json.dumps(s if s is not None else "", ensure_ascii=False)


def build_categories(pages):
    counts = {}
    for pg in pages:
        props = pg.get("properties", {})
        cats = prop_multiselect(props, "카테고리")
        for c in cats:
            counts[c] = counts.get(c, 0) + 1

    ordered = [c for c in CATEGORY_ORDER if c in counts]
    # 정의된 순서에 없는 새 카테고리는 뒤에 개수 내림차순으로 추가
    extra = sorted(
        [c for c in counts if c not in CATEGORY_ORDER],
        key=lambda c: -counts[c],
    )
    final_order = ordered + extra

    lines = []
    for c in final_order:
        lines.append(f'  {{n:{js_str(c)},v:{counts[c]}}}')
    return "[\n" + ",\n".join(lines) + "\n]"


def build_evidence(pages):
    items = []
    for pg in pages:
        props = pg.get("properties", {})
        cats = prop_multiselect(props, "카테고리")
        if "P-CAB" not in cats:
            continue

        title = prop_text(props, "제목")
        rtype = prop_select(props, "자료유형")
        journal = prop_text(props, "저널정보")
        author = prop_text(props, "저자")
        pmid = prop_text(props, "PMID") or prop_text(props, "pmid")
        link = prop_url(props, "링크")
        date = prop_date(props, "등록일")
        abstract = prop_text(props, "초록요약")

        if not title:
            continue

        items.append({
            "title": title, "type": rtype, "journal": journal,
            "author": author, "cats": cats, "pmid": pmid,
            "link": link, "date": date, "abs": abstract,
        })

    # 등록일 내림차순 정렬 (빈 날짜는 맨 뒤로) - 실제 저널 발행일이 아니라
    # Notion DB에 등록(발견)된 시점 기준으로 "최신 문헌" 목록을 만든다.
    items.sort(key=lambda x: x["date"] or "0000-00-00", reverse=True)

    lines = []
    for it in items:
        cats_js = "[" + ",".join(js_str(c) for c in it["cats"]) + "]"
        lines.append(
            "{"
            f'title:{js_str(it["title"])},'
            f'type:{js_str(it["type"])},'
            f'journal:{js_str(it["journal"])},'
            f'author:{js_str(it["author"])},'
            f'cats:{cats_js},'
            f'pmid:{js_str(it["pmid"])},'
            f'link:{js_str(it["link"])},'
            f'date:{js_str(it["date"])},'
            f'abs:{js_str(it["abs"])}'
            "}"
        )
    return "[\n " + ",\n ".join(lines) + "\n]"


def kst_today_str():
    """실행 시점(스크립트가 돌아간 날) 기준 한국시간(KST) 날짜를 YYYY-MM-DD로 반환"""
    now_kst = datetime.now(timezone.utc) + timedelta(hours=9)
    return now_kst.strftime("%Y-%m-%d")


def sync_footer_date(html):
    """footer의 '데이터 기준일 YYYY-MM-DD'를 오늘 날짜(KST)로 자동 갱신"""
    today = kst_today_str()
    pattern = re.compile(r"데이터 기준일 \d{4}-\d{2}-\d{2}")
    new_html, n = pattern.subn(f"데이터 기준일 {today}", html, count=1)
    if n == 0:
        print("[WARN] '데이터 기준일' 표기를 찾지 못해 갱신을 건너뜁니다.")
        return html
    print(f"[INFO] 데이터 기준일을 {today}로 갱신했습니다.")
    return new_html


def replace_block(html, var_name, new_array_literal):
    """`const <var_name>=[ ... ];` 블록을 통째로 새 배열로 교체"""
    pattern = re.compile(
        r"const\s+" + re.escape(var_name) + r"\s*=\s*\[.*?\];",
        re.DOTALL,
    )
    replacement = f"const {var_name}={new_array_literal};"
    new_html, n = pattern.subn(replacement, html, count=1)
    if n == 0:
        die(f"'{var_name}' 배열 블록을 index.html에서 찾지 못했습니다. "
            f"HTML 구조가 변경되었는지 확인하세요.")
    return new_html


# =========================================================================
# 2) 메인 페이지 본문 블록 동기화
#    (Hero 소개문 / Executive Snapshot / Evidence Map / Clinical Implications
#     / Strategic Watchlist / Bottom Line)
# =========================================================================

NO_RECURSE_TYPES = {"child_database", "child_page", "link_to_page"}


def fetch_children(block_id):
    results = []
    cursor = None
    while True:
        qs = "?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        data = notion_get(f"/blocks/{block_id}/children{qs}")
        results.extend(data.get("results", []))
        if data.get("has_more"):
            cursor = data.get("next_cursor")
            time.sleep(0.2)
        else:
            break
    return results


def fetch_tree(block_id, depth=0, max_depth=6):
    """block_id의 자식 블록을 재귀적으로 모두 가져와 각 블록에 children 키를 채운다."""
    children = fetch_children(block_id)
    if depth < max_depth:
        for b in children:
            if b.get("has_children") and b.get("type") not in NO_RECURSE_TYPES:
                b["children"] = fetch_tree(b["id"], depth + 1, max_depth)
            else:
                b.setdefault("children", [])
    return children


def rt_plain(rich_text):
    return "".join(x.get("plain_text", "") for x in (rich_text or []))


def rt_html(rich_text):
    """rich_text 배열을 안전한 HTML로 변환 (bold만 <b>로 보존)"""
    out = []
    for x in (rich_text or []):
        t = htmlmod.escape(x.get("plain_text", ""))
        if x.get("annotations", {}).get("bold"):
            t = f"<b>{t}</b>"
        out.append(t)
    return "".join(out)


def block_text(block):
    """블록 타입별 rich_text를 꺼낸다."""
    t = block.get("type")
    data = block.get(t, {})
    return data.get("rich_text", [])


def find_heading_index(blocks, contains):
    for i, b in enumerate(blocks):
        if b.get("type") in ("heading_1", "heading_2", "heading_3"):
            if contains in rt_plain(block_text(b)):
                return i
    return -1


def next_block_after(blocks, idx, target_type=None):
    """idx 다음에 나오는 첫 번째 non-divider 블록을 반환 (타입은 호출부에서 검증)"""
    for b in blocks[idx + 1:]:
        if b.get("type") == "divider":
            continue
        return b
    return None


def collect_bullets_recursive(block, out):
    """블록 트리에서 bulleted_list_item 텍스트를 문서 순서대로 전부 수집"""
    if block.get("type") == "bulleted_list_item":
        out.append(rt_html(block_text(block)))
    for child in block.get("children", []):
        collect_bullets_recursive(child, out)


def parse_card_column(col):
    """Executive Snapshot / Clinical Implications의 column 하나를 파싱해
    (아이콘+제목 HTML, 불릿 HTML 리스트) 를 반환"""
    title_html = ""
    kids = col.get("children", [])
    if kids:
        first = kids[0]
        if first.get("type") == "callout":
            icon = first.get("callout", {}).get("icon", {})
            icon_ch = icon.get("emoji", "") if icon.get("type") == "emoji" else ""
            title_txt = rt_html(block_text(first))
            title_html = f"{icon_ch} {title_txt}".strip()
        elif first.get("type") in ("heading_1", "heading_2", "heading_3"):
            title_html = rt_html(block_text(first))

    bullets = []
    for k in kids:
        collect_bullets_recursive(k, bullets)
    return title_html, bullets


def build_cards3_html(blocks, heading_text, card_class):
    """column_list 기반 3카드 섹션(HTML)을 만든다. 실패하면 None."""
    hidx = find_heading_index(blocks, heading_text)
    if hidx == -1:
        return None
    col_list = next_block_after(blocks, hidx, target_type="column_list")
    if not col_list or col_list.get("type") != "column_list":
        return None

    cards = []
    for col in col_list.get("children", []):
        if col.get("type") != "column":
            continue
        title_html, bullets = parse_card_column(col)
        if not title_html:
            continue
        lis = "\n          ".join(f"<li>{b}</li>" for b in bullets)
        cards.append(
            f'      <div class="{card_class}">\n'
            f'        <h3>{title_html}</h3>\n'
            f'        <ul>\n          {lis}\n        </ul>\n'
            f'      </div>'
        )
    if not cards:
        return None
    return '<div class="cards3">\n' + "\n".join(cards) + "\n    </div>"


def build_hero_lead(blocks):
    """맨 위 인트로 callout에서 두번째 줄(소개 문구)을 추출"""
    for b in blocks:
        if b.get("type") == "callout":
            full = rt_plain(block_text(b))
            parts = full.split("\n", 1)
            if len(parts) > 1:
                return htmlmod.escape(parts[1].strip())
            return htmlmod.escape(full.strip())
        if b.get("type") in ("heading_1", "heading_2", "heading_3"):
            break  # 첫 heading 전에 callout이 없으면 포기
    return None


LEVEL_HIGH = {"높음"}
LEVEL_LOW = {"낮음"}


def level_class(text):
    if "낮음" in text and "중간" not in text and "높음" not in text:
        return "low"
    if "높음" in text and "중간" not in text:
        return "high"
    return "mid"


def build_evidence_map_html(blocks):
    hidx = find_heading_index(blocks, "P-CAB Evidence Map")
    if hidx == -1:
        return None
    table = next_block_after(blocks, hidx, target_type="table")
    if not table or table.get("type") != "table":
        return None

    rows = table.get("children", [])
    if len(rows) < 2:
        return None

    body_rows = []
    for r in rows[1:]:  # 첫 행은 헤더이므로 skip
        cells = r.get("table_row", {}).get("cells", [])
        if len(cells) < 4:
            continue
        area = rt_html(cells[0])
        level_txt = rt_plain(cells[1])
        level_html = rt_html(cells[1])
        point = rt_html(cells[2])
        interp = rt_html(cells[3])
        cls = level_class(level_txt)
        body_rows.append(
            f'<tr><td class="area">{area}</td>'
            f'<td><span class="lvl {cls}">{level_html}</span></td>'
            f'<td>{point}</td><td>{interp}</td></tr>'
        )
    if not body_rows:
        return None
    return "\n        ".join(body_rows)


def build_watchlist_html(blocks):
    hidx = find_heading_index(blocks, "Strategic Watchlist")
    if hidx == -1:
        return None
    items = []
    for b in blocks[hidx + 1:]:
        if b.get("type") == "divider":
            break
        if b.get("type") == "to_do":
            items.append(rt_html(block_text(b)))
    if not items:
        return None
    lines = "\n      ".join(
        f'<div class="witem"><span class="dot"></span>{t}</div>' for t in items
    )
    return lines


def build_bottom_line_html(blocks):
    hidx = find_heading_index(blocks, "Bottom Line")
    if hidx == -1:
        return None
    callout = next_block_after(blocks, hidx, target_type="callout")
    if not callout or callout.get("type") != "callout":
        return None
    return rt_html(block_text(callout))


def replace_section_html(html, label, open_marker_regex, close_marker, new_inner):
    """open_marker_regex(정규식, 캡처 없음) 바로 뒤 ~ close_marker(리터럴 문자열) 앞까지를
    new_inner로 교체한다. 못 찾으면 원본 그대로 반환하고 경고만 출력."""
    m = re.search(open_marker_regex, html, re.DOTALL)
    if not m:
        print(f"[WARN] '{label}' 시작 위치를 찾지 못해 건너뜁니다.")
        return html
    start = m.end()
    end = html.find(close_marker, start)
    if end == -1:
        print(f"[WARN] '{label}' 종료 위치를 찾지 못해 건너뜁니다.")
        return html
    return html[:start] + new_inner + html[end:]


def sync_page_content(html, page_id):
    print(f"[INFO] Notion 페이지 본문 조회 중... ({page_id})")
    blocks = fetch_tree(page_id, max_depth=5)
    print(f"[INFO] 페이지 최상위 블록 {len(blocks)}개 수집.")

    # 1) Hero 소개 문구
    lead = build_hero_lead(blocks)
    if lead:
        html = replace_section_html(
            html, "Hero 소개 문구",
            r'<p class="lead">', r'</p>', lead,
        )

    # 2) Executive Snapshot (accent-top 카드)
    exec_html = build_cards3_html(blocks, "Executive Snapshot", "icard accent-top")
    if exec_html:
        html = replace_section_html(
            html, "Executive Snapshot",
            r'<span class="en">Executive Snapshot</span>[^<]*</h2>\s*',
            "\n  </div>\n</section>",
            exec_html + "\n  ",
        )

    # 3) P-CAB Evidence Map 표
    map_rows = build_evidence_map_html(blocks)
    if map_rows:
        html = replace_section_html(
            html, "P-CAB Evidence Map",
            r'<tbody>\s*', "</tbody>", map_rows + "\n      ",
        )

    # 4) Clinical Implications (accent-top 없는 카드)
    ci_html = build_cards3_html(blocks, "Clinical Implications", "icard")
    if ci_html:
        html = replace_section_html(
            html, "Clinical Implications",
            r'<span class="en">Clinical Implications</span>[^<]*</h2>\s*',
            "\n  </div>\n</section>",
            ci_html + "\n  ",
        )

    # 5) Strategic Watchlist
    watch_html = build_watchlist_html(blocks)
    if watch_html:
        html = replace_section_html(
            html, "Strategic Watchlist",
            r'<div class="watch">\s*', "\n  </div>\n</section>",
            "\n      " + watch_html + "\n    </div>",
        )

    # 6) Bottom Line
    bl_html = build_bottom_line_html(blocks)
    if bl_html:
        html = replace_section_html(
            html, "Bottom Line",
            r'<h3>🎯 Bottom Line</h3>\s*<p>', r'</p>', bl_html,
        )

    return html


def main():
    if not NOTION_TOKEN:
        die("환경변수 NOTION_TOKEN이 설정되어 있지 않습니다.")
    if not DATABASE_ID:
        die("환경변수 DATABASE_ID가 설정되어 있지 않습니다.")
    if not os.path.exists(INDEX_HTML):
        die(f"{INDEX_HTML} 파일을 찾을 수 없습니다.")

    print(f"[INFO] Notion 데이터베이스 조회 중... ({DATABASE_ID})")
    pages = fetch_all_pages(DATABASE_ID)
    print(f"[INFO] 총 {len(pages)}건의 문헌을 가져왔습니다.")

    categories_js = build_categories(pages)
    evidence_js = build_evidence(pages)
    evidence_count = evidence_js.count("title:")
    print(f"[INFO] P-CAB 카테고리 문헌 {evidence_count}건을 갤러리에 반영합니다.")

    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        html = f.read()

    html = replace_block(html, "categories", categories_js)
    html = replace_block(html, "evidence", evidence_js)

    if PAGE_ID:
        html = sync_page_content(html, PAGE_ID)
    else:
        print("[INFO] PAGE_ID가 설정되지 않아 페이지 본문(Executive Snapshot 등) "
              "동기화는 건너뜁니다.")

    html = sync_footer_date(html)

    with open(INDEX_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[INFO] {INDEX_HTML} 갱신 완료.")


if __name__ == "__main__":
    main()
