#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Notion 데이터베이스("위산분비질환 문헌 업데이트")에서 문헌 데이터를 가져와
index.html 안의 categories / evidence JS 배열을 최신 내용으로 교체한다.

환경변수:
  NOTION_TOKEN   - Notion Integration access token (필수)
  DATABASE_ID    - Notion 데이터베이스 ID (필수)
  INDEX_HTML     - 수정할 html 경로 (기본값 index.html)

동작:
  1. Notion API로 데이터베이스 전체 페이지를 페이지네이션하며 가져온다.
  2. 카테고리(멀티선택) 값 전체를 집계해 categories 배열을 만든다.
     (Notion 페이지의 "Evidence Categories" 카운트와 동일한 로직: 전체 DB 기준 집계)
  3. "카테고리"에 P-CAB이 포함된 문헌만 추려 evidence 배열을 만들고
     출판일자 내림차순으로 정렬한다.
  4. index.html 안의 `const categories=[...]` 와 `const evidence=[...]`
     블록만 정규식으로 찾아 새 데이터로 교체한다. 나머지 HTML/CSS/렌더링
     로직은 건드리지 않는다.
"""

import os
import re
import sys
import json
import time
import urllib.request
import urllib.error

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "").strip()
DATABASE_ID = os.environ.get("DATABASE_ID", "").strip()
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
        date = prop_date(props, "출판일자")
        abstract = prop_text(props, "초록요약")

        if not title:
            continue

        items.append({
            "title": title, "type": rtype, "journal": journal,
            "author": author, "cats": cats, "pmid": pmid,
            "link": link, "date": date, "abs": abstract,
        })

    # 출판일자 내림차순 정렬 (빈 날짜는 맨 뒤로)
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
    evidence_count = evidence_js.count('"title"') if False else evidence_js.count("title:")
    print(f"[INFO] P-CAB 카테고리 문헌 {evidence_count}건을 갤러리에 반영합니다.")

    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        html = f.read()

    html = replace_block(html, "categories", categories_js)
    html = replace_block(html, "evidence", evidence_js)

    with open(INDEX_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[INFO] {INDEX_HTML} 갱신 완료.")


if __name__ == "__main__":
    main()
