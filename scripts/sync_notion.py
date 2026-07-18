#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Notion에서 두 종류의 소스를 가져와 index.html을 갱신한다. (2차 재설계 버전)

페이지에는 이제 4개 섹션 + 표지만 존재한다:
  1) Evidence Landscape        - 표
  2) DB 기반 최신 동향 분석      - 인트로 콜아웃 + N개 하위 subsection
                                  (heading_3로 시작, 그 아래 column_list 또는
                                  table이 따라오는 구조) + 마지막 트레일링 콜아웃
  3) Monthly Bottom Line        - 라벨:내용 형태의 여러 줄로 된 콜아웃
  4) 위산분비질환 문헌 업데이트   - Notion 데이터베이스(P-CAB 카테고리) 갤러리

동작 원리: 각 섹션을 "통째로" 새로 생성해서, index.html 안의
`<!-- 섹션이름 -->` 주석부터 그 섹션의 `</section>`까지를 전부 교체한다.
(개별 조각을 정규식으로 patch하는 대신, 섹션 전체를 재생성하므로
Notion 쪽 하위 구조가 바뀌어도 비교적 안정적으로 반영된다.)

환경변수:
  NOTION_TOKEN   - Notion Integration access token (필수)
  DATABASE_ID    - Notion 데이터베이스 ID (필수)
  PAGE_ID        - Notion 메인 페이지 ID (선택. 없으면 DB 갤러리만 갱신)
  INDEX_HTML     - 수정할 html 경로 (기본값 index.html)
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


def die(msg):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def notion_request(path, payload=None):
    url = f"{API_BASE}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method="POST" if payload is not None else "GET",
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
        url, method="GET",
        headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": NOTION_VERSION},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        die(f"Notion API 오류 {e.code}: {body}")


# =========================================================================
# 1) 데이터베이스 -> 문헌 갤러리(evidence 배열)
# =========================================================================

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
            time.sleep(0.3)
        else:
            break
    return results


def prop_text(props, name):
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


def js_str(s):
    return json.dumps(s if s is not None else "", ensure_ascii=False)


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
        date = prop_date(props, "등록일")       # 보조 정렬 기준
        pubdate = prop_date(props, "출판일자")   # 주 정렬 기준 + 화면 표시용
        abstract = prop_text(props, "초록요약")

        if not title:
            continue

        items.append({
            "title": title, "type": rtype, "journal": journal,
            "author": author, "cats": cats, "pmid": pmid,
            "link": link, "date": date, "pubdate": pubdate, "abs": abstract,
        })

    # 정렬: 1차 출판일자(실제 발행일) 내림차순, 2차 등록일 내림차순.
    items.sort(
        key=lambda x: (x["pubdate"] or "0000-00-00", x["date"] or "0000-00-00"),
        reverse=True,
    )

    lines = []
    for it in items:
        cats_js = "[" + ",".join(js_str(c) for c in it["cats"]) + "]"
        display_date = it["pubdate"] or it["date"]
        lines.append(
            "{"
            f'title:{js_str(it["title"])},'
            f'type:{js_str(it["type"])},'
            f'journal:{js_str(it["journal"])},'
            f'author:{js_str(it["author"])},'
            f'cats:{cats_js},'
            f'pmid:{js_str(it["pmid"])},'
            f'link:{js_str(it["link"])},'
            f'date:{js_str(display_date)},'
            f'abs:{js_str(it["abs"])}'
            "}"
        )
    return "[\n " + ",\n ".join(lines) + "\n]"


def replace_evidence_array(html, new_array_literal):
    pattern = re.compile(r"const\s+evidence\s*=\s*\[.*?\];", re.DOTALL)
    new_html, n = pattern.subn(f"const evidence={new_array_literal};", html, count=1)
    if n == 0:
        die("'evidence' 배열 블록을 index.html에서 찾지 못했습니다.")
    return new_html


# =========================================================================
# 2) 메인 페이지 본문 -> Evidence Landscape / DB 기반 최신 동향 분석 /
#    Monthly Bottom Line
# =========================================================================

NO_RECURSE_TYPES = {"child_database", "child_page", "link_to_page"}

# "DB 기반 최신 동향 분석" 하위 subsection 중, 제목에 아래 문자열이 포함되면
# 웹사이트에는 숨기고 건너뛴다. (Notion에는 그대로 남아있어도 무방)
HIDDEN_SUBSECTIONS = ["전략적 시사점"]


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
    out = []
    for x in (rich_text or []):
        t = htmlmod.escape(x.get("plain_text", ""))
        if x.get("annotations", {}).get("bold"):
            t = f"<b>{t}</b>"
        out.append(t)
    return "".join(out)


def block_text(block):
    t = block.get("type")
    data = block.get(t, {})
    return data.get("rich_text", [])


def find_heading_index(blocks, contains):
    for i, b in enumerate(blocks):
        if b.get("type") in ("heading_1", "heading_2", "heading_3"):
            if contains in rt_plain(block_text(b)):
                return i
    return -1


def next_block_after(blocks, idx):
    for b in blocks[idx + 1:]:
        if b.get("type") == "divider":
            continue
        return b
    return None


def collect_bullets_recursive(block, out):
    if block.get("type") == "bulleted_list_item":
        out.append(rt_html(block_text(block)))
    for child in block.get("children", []):
        collect_bullets_recursive(child, out)


def parse_card_column(col):
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


def level_class(text):
    """🟢🟡🟠🔴 등 색상 표기를 CSS 클래스로 매핑"""
    if "높음" in text and "중간" not in text:
        return "high"
    if "중간" in text:
        return "mid"
    return "low"


LEVEL_EMOJIS = ("🟢", "🟡", "🟠", "🔴")


def gen_generic_table(table_block):
    rows = table_block.get("children", [])
    if len(rows) < 1:
        return None
    header_cells = rows[0].get("table_row", {}).get("cells", [])
    th_html = "".join(f"<th>{rt_html(c)}</th>" for c in header_cells)

    body_rows = []
    for r in rows[1:]:
        cells = r.get("table_row", {}).get("cells", [])
        tds = []
        for i, c in enumerate(cells):
            text_html = rt_html(c)
            text_plain = rt_plain(c)
            if i == 0:
                tds.append(f'<td class="area">{text_html}</td>')
            elif any(sym in text_plain for sym in LEVEL_EMOJIS):
                cls = level_class(text_plain)
                tds.append(f'<td><span class="lvl {cls}">{text_html}</span></td>')
            else:
                tds.append(f"<td>{text_html}</td>")
        body_rows.append("<tr>" + "".join(tds) + "</tr>")

    if not body_rows:
        return None
    return (
        '<table class="evmap">\n<thead><tr>' + th_html + "</tr></thead>\n"
        "<tbody>\n" + "\n".join(body_rows) + "\n</tbody>\n</table>"
    )


def replace_full_section(html, comment_marker, new_section_html, label=None):
    label = label or comment_marker
    pattern = re.compile(re.escape(f"<!-- {comment_marker} -->") + r".*?</section>", re.DOTALL)
    if not pattern.search(html):
        print(f"[WARN] '{label}' 섹션 마커를 index.html에서 찾지 못해 건너뜁니다.")
        return html
    return pattern.sub(lambda m: new_section_html, html, count=1)


# ---- Evidence Landscape ----

def gen_evidence_landscape_section(blocks):
    hidx = find_heading_index(blocks, "Evidence Landscape")
    if hidx == -1:
        print("[WARN] 'Evidence Landscape' 제목을 Notion 페이지에서 찾지 못했습니다.")
        return None
    table_block = next_block_after(blocks, hidx)
    if not table_block or table_block.get("type") != "table":
        print("[WARN] 'Evidence Landscape' 표를 찾지 못했습니다.")
        return None
    table_html = gen_generic_table(table_block)
    if not table_html:
        return None

    return (
        "<!-- Evidence Landscape -->\n"
        '<section class="section">\n'
        '  <div class="wrap">\n'
        '    <h2 class="sec-title"><span class="en">Evidence Landscape</span>임상영역별 근거 지도</h2>\n'
        f"    {table_html}\n"
        "  </div>\n"
        "</section>"
    )


# ---- DB 기반 최신 동향 분석 ----

def gen_trend_analysis_section(blocks):
    hidx = find_heading_index(blocks, "DB 기반 최신 동향 분석")
    if hidx == -1:
        print("[WARN] 'DB 기반 최신 동향 분석' 제목을 Notion 페이지에서 찾지 못했습니다.")
        return None

    intro_block = next_block_after(blocks, hidx)
    intro_html = ""
    start_scan = hidx + 1
    if intro_block is not None and intro_block.get("type") == "callout":
        intro_html = rt_html(block_text(intro_block))
        try:
            start_scan = blocks.index(intro_block) + 1
        except ValueError:
            pass

    subsections = []
    current_title = None
    current_body = []
    trailing_bl_html = ""

    def flush():
        if current_title is not None:
            if any(kw in current_title for kw in HIDDEN_SUBSECTIONS):
                print(f"[INFO] '{current_title}' subsection은 숨김 설정으로 건너뜁니다.")
                return
            subsections.append(
                f'<div class="subsection"><h3 class="subhead">{current_title}</h3>'
                + "".join(current_body) + "</div>"
            )

    i = start_scan
    while i < len(blocks):
        b = blocks[i]
        t = b.get("type")
        if t in ("heading_1", "heading_2"):
            break
        if t == "heading_3":
            flush()
            current_title = rt_html(block_text(b))
            current_body = []
        elif t == "column_list":
            cards = []
            for col in b.get("children", []):
                if col.get("type") != "column":
                    continue
                title_html, bullets = parse_card_column(col)
                if not title_html:
                    continue
                lis = "".join(f"<li>{x}</li>" for x in bullets)
                cards.append(f'<div class="icard"><h3>{title_html}</h3><ul>{lis}</ul></div>')
            if cards:
                current_body.append('<div class="cards3">' + "".join(cards) + "</div>")
        elif t == "table":
            tbl = gen_generic_table(b)
            if tbl:
                current_body.append(tbl)
        elif t == "callout":
            trailing_bl_html = rt_html(block_text(b))
        i += 1
    flush()

    if not subsections:
        print("[WARN] 'DB 기반 최신 동향 분석' 하위 subsection을 하나도 못 찾았습니다.")
        return None

    bl_block = ""
    if trailing_bl_html:
        bl_block = (
            '<div class="subsection"><div class="bottomline">'
            "<h3>🎯 DB 기반 Bottom Line</h3>"
            f"<p>{trailing_bl_html}</p></div></div>"
        )

    return (
        "<!-- DB 기반 최신 동향 분석 -->\n"
        '<section class="section" style="background:#ffe6cc;">\n'
        '  <div class="wrap">\n'
        '    <h2 class="sec-title"><span class="en">DB 기반 최신 동향 분석</span>Evidence Trend Analysis</h2>\n'
        f'    <div class="analysis-note">{intro_html}</div>\n'
        + "".join(subsections) + bl_block +
        "\n  </div>\n</section>"
    )


# ---- Monthly Bottom Line ----

def gen_monthly_bottomline_section(blocks):
    hidx = find_heading_index(blocks, "Monthly Bottom Line")
    if hidx == -1:
        print("[WARN] 'Monthly Bottom Line' 제목을 Notion 페이지에서 찾지 못했습니다.")
        return None
    callout = next_block_after(blocks, hidx)
    if not callout or callout.get("type") != "callout":
        print("[WARN] 'Monthly Bottom Line' 콜아웃을 찾지 못했습니다.")
        return None

    full = rt_plain(block_text(callout)).replace("<br>", "\n").replace("<br/>", "\n")
    lines = [l.strip() for l in full.split("\n") if l.strip()]
    if not lines:
        return None

    parts = []
    for line in lines:
        m = re.match(r"^([^:：]{1,24})[:：]\s*(.*)$", line)
        if m:
            label = htmlmod.escape(m.group(1).strip())
            content = htmlmod.escape(m.group(2).strip())
            parts.append(f'<p class="bl-item"><b>{label}</b>: {content}</p>')
        else:
            parts.append(f'<p class="bl-item">{htmlmod.escape(line)}</p>')

    ref_html = (
        '<div class="bl-ref">Notion 문헌 데이터베이스에서 관련 최신 문헌을 함께 확인할 수 있습니다: '
        f'<a href="https://app.notion.com/p/{DATABASE_ID}" target="_blank">문헌 DB에서 보기 →</a></div>'
    )

    return (
        "<!-- Monthly Bottom Line -->\n"
        '<section class="section" style="background:#ffe6cc;">\n'
        '  <div class="wrap">\n'
        '    <div class="bottomline">\n'
        "      <h3>🎯 Monthly Bottom Line</h3>\n"
        + "\n".join(parts) + "\n"
        + ref_html +
        "\n    </div>\n  </div>\n</section>"
    )


# ---- Hero 소개 문구 ----

def build_hero_lead(blocks):
    for b in blocks:
        if b.get("type") == "callout":
            full = rt_plain(block_text(b))
            parts = full.split("\n", 1)
            return htmlmod.escape((parts[1] if len(parts) > 1 else full).strip())
        if b.get("type") in ("heading_1", "heading_2", "heading_3"):
            break
    return None


def sync_page_content(html, page_id):
    print(f"[INFO] Notion 페이지 본문 조회 중... ({page_id})")
    blocks = fetch_tree(page_id, max_depth=6)
    print(f"[INFO] 페이지 최상위 블록 {len(blocks)}개 수집.")

    lead = build_hero_lead(blocks)
    if lead:
        m = re.search(r'<p class="lead">', html)
        if m:
            start = m.end()
            end = html.find("</p>", start)
            if end != -1:
                html = html[:start] + lead + html[end:]
        else:
            print("[WARN] Hero 소개 문구 위치를 찾지 못해 건너뜁니다.")

    ev_html = gen_evidence_landscape_section(blocks)
    if ev_html:
        html = replace_full_section(html, "Evidence Landscape", ev_html)

    trend_html = gen_trend_analysis_section(blocks)
    if trend_html:
        html = replace_full_section(html, "DB 기반 최신 동향 분석", trend_html)

    bl_html = gen_monthly_bottomline_section(blocks)
    if bl_html:
        html = replace_full_section(html, "Monthly Bottom Line", bl_html)

    return html


# =========================================================================
# 3) footer 날짜 자동 갱신
# =========================================================================

def kst_today_str():
    now_kst = datetime.now(timezone.utc) + timedelta(hours=9)
    return now_kst.strftime("%Y-%m-%d")


def sync_footer_date(html):
    today = kst_today_str()
    pattern = re.compile(r"데이터 기준일 \d{4}-\d{2}-\d{2}")
    new_html, n = pattern.subn(f"데이터 기준일 {today}", html, count=1)
    if n == 0:
        print("[WARN] '데이터 기준일' 표기를 찾지 못해 갱신을 건너뜁니다.")
        return html
    print(f"[INFO] 데이터 기준일을 {today}로 갱신했습니다.")
    return new_html


# =========================================================================
# main
# =========================================================================

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

    evidence_js = build_evidence(pages)
    evidence_count = evidence_js.count("title:")
    print(f"[INFO] P-CAB 카테고리 문헌 {evidence_count}건을 갤러리에 반영합니다.")

    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        html = f.read()

    html = replace_evidence_array(html, evidence_js)

    if PAGE_ID:
        html = sync_page_content(html, PAGE_ID)
    else:
        print("[INFO] PAGE_ID가 설정되지 않아 페이지 본문 동기화는 건너뜁니다.")

    html = sync_footer_date(html)

    with open(INDEX_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[INFO] {INDEX_HTML} 갱신 완료.")


if __name__ == "__main__":
    main()
