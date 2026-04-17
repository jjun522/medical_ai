"""
의료 AI 논문 자동 정리 스크립트
매일 GitHub Actions로 실행되어 Claude API로 논문을 정리하고
Notion DB에 저장 + Slack으로 알림을 보냅니다.
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import anthropic
import requests


# ==================== 설정 ====================

# 시작 날짜 (Day 1이 되는 날). 본인 시작일로 바꾸세요.
START_DATE = datetime(2026, 4, 20, tzinfo=timezone(timedelta(hours=9)))

# Claude 모델
CLAUDE_MODEL = "claude-opus-4-5"

# 환경변수에서 API 키 로드 (GitHub Secrets에서 주입됨)
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
NOTION_API_KEY = os.environ["NOTION_API_KEY"]
NOTION_DB_ID = os.environ["NOTION_DB_ID"]
SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]


# ==================== 1. 오늘의 논문 선택 ====================

def get_today_paper():
    """시작일 기준으로 오늘이 Day N인지 계산해서 해당 논문을 리턴"""
    # 한국 시간 기준
    kst = timezone(timedelta(hours=9))
    today = datetime.now(kst)
    days_passed = (today.date() - START_DATE.date()).days + 1

    if days_passed < 1:
        print(f"아직 시작일({START_DATE.date()}) 전입니다.")
        sys.exit(0)

    # 30편 다 읽으면 순환 (원하면 sys.exit로 바꿔서 멈추게 해도 됨)
    day_number = ((days_passed - 1) % 30) + 1

    papers_file = Path(__file__).parent / "papers_30.json"
    with open(papers_file, encoding="utf-8") as f:
        data = json.load(f)

    paper = next((p for p in data["papers"] if p["day"] == day_number), None)
    if not paper:
        raise ValueError(f"Day {day_number} 논문을 찾을 수 없습니다")

    print(f"📚 Day {day_number}: {paper['title']}")
    return paper


# ==================== 2. Claude API로 정리 ====================

SUMMARY_PROMPT = """당신은 의료 AI 논문을 초보자에게 설명하는 전문가입니다. 아래 논문을 공학 전공이지만 의료 AI는 처음인 초보자 눈높이로 정리해주세요.

논문 정보:
- 제목: {title}
- 저자: {authors}
- 연도: {year}
- 학회/저널: {venue}
- URL: {url}
- 이 논문을 읽는 이유: {why_read}

다음 형식으로 **한국어 마크다운**으로 작성해주세요. 각 섹션은 구체적으로 작성하되, 너무 길지 않게 핵심만 담아주세요.

## 📌 한 줄 요약
(한 문장으로. "이 논문은 ___ 문제를 ___ 방법으로 해결했다." 형태)

## 🔑 핵심 키워드
- (관련 키워드 3-5개)

## 1️⃣ 왜 이 논문인가?
**기존 방법의 한계:**
- 

**이 논문이 풀고자 한 문제:**
- 

## 2️⃣ 핵심 아이디어
**한 마디로:** 
**구체적으로:**
- 
- 

## 3️⃣ 방법론
**전체 구조:** (아키텍처를 텍스트로 간단히)

**주요 컴포넌트:**
1. 
2. 

**학습 설정:** (Dataset, Loss 등 핵심만)

## 4️⃣ 실험 결과
- 주요 데이터셋:
- 성능:
- 중요한 발견:

## 5️⃣ 기여
(이 논문이 새롭게 보여준 것)

## 6️⃣ 한계
- 저자가 언급한 한계:
- 의료 현장 적용 시 문제될 수 있는 점:

## 7️⃣ 초보자가 알아야 할 개념
(논문에 나오는 중요 용어 3-5개를 각각 2-3문장으로 쉽게 설명)

## 💬 읽을 때 팁
(어느 섹션에 집중하면 좋은지, 건너뛰어도 되는 부분은 뭔지)

---
⚠️ 주의: 이 정리는 초안입니다. 반드시 원문을 읽고 직접 검증하세요."""


def summarize_paper(paper):
    """Claude API로 논문 정리 초안 생성"""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = SUMMARY_PROMPT.format(**paper)

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    summary = message.content[0].text
    print(f"✅ Claude 정리 완료 ({len(summary)}자)")
    return summary


# ==================== 3. Notion에 페이지 생성 ====================

def markdown_to_notion_blocks(markdown_text):
    """마크다운 텍스트를 Notion block 객체 리스트로 변환 (간단 버전)"""
    blocks = []
    lines = markdown_text.split("\n")

    for line in lines:
        stripped = line.strip()
        if not stripped:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": []}
            })
            continue

        # Heading 2
        if stripped.startswith("## "):
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": stripped[3:]}}]
                }
            })
        # Heading 3
        elif stripped.startswith("### "):
            blocks.append({
                "object": "block",
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": stripped[4:]}}]
                }
            })
        # Bullet
        elif stripped.startswith("- "):
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": stripped[2:]}}]
                }
            })
        # 번호 리스트
        elif len(stripped) > 2 and stripped[0].isdigit() and stripped[1:3] in (". ", "- "):
            blocks.append({
                "object": "block",
                "type": "numbered_list_item",
                "numbered_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": stripped[3:]}}]
                }
            })
        # 구분선
        elif stripped == "---":
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        # 일반 문단
        else:
            # Notion은 한 rich_text 내용이 2000자 넘으면 에러
            content = stripped[:1990]
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": content}}]
                }
            })

    return blocks


def create_notion_page(paper, summary):
    """Notion DB에 새 페이지 생성"""
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    # 한국 시간 오늘 날짜
    kst = timezone(timedelta(hours=9))
    today_iso = datetime.now(kst).strftime("%Y-%m-%d")

    title = f"Day {paper['day']}: {paper['short_name']} - {paper['title']}"

    properties = {
        "이름": {
            "title": [{"text": {"content": title[:2000]}}]
        },
        "분야": {
            "multi_select": [{"name": paper["category"]}]
        },
        "난이도": {
            "select": {"name": paper["difficulty"]}
        },
        "저자": {
            "rich_text": [{"text": {"content": paper["authors"]}}]
        },
        "연도": {
            "number": paper["year"]
        },
        "링크": {
            "url": paper["url"]
        },
        "읽은 날짜": {
            "date": {"start": today_iso}
        },
    }

    blocks = markdown_to_notion_blocks(summary)

    # Notion API는 한 번에 최대 100블록
    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": properties,
        "children": blocks[:100],
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)

    if resp.status_code != 200:
        print(f"❌ Notion 에러: {resp.status_code}")
        print(resp.text)
        resp.raise_for_status()

    page = resp.json()
    page_url = page.get("url", "")
    page_id = page["id"]
    print(f"✅ Notion 페이지 생성: {page_url}")

    # 블록이 100개 넘으면 나눠서 추가
    if len(blocks) > 100:
        append_url = f"https://api.notion.com/v1/blocks/{page_id}/children"
        for i in range(100, len(blocks), 100):
            chunk = blocks[i:i + 100]
            r = requests.patch(
                append_url, headers=headers,
                json={"children": chunk}, timeout=30
            )
            r.raise_for_status()

    return page_url


# ==================== 4. Slack 알림 ====================

def send_slack_notification(paper, notion_url):
    """Slack Incoming Webhook으로 알림 전송"""
    text = (
        f"📚 *Day {paper['day']} 의료 AI 논문이 도착했어요*\n\n"
        f"*{paper['title']}*\n"
        f"👥 {paper['authors']} ({paper['year']})\n"
        f"🏛 {paper['venue']}  |  ⭐ {paper['difficulty']}\n\n"
        f"💡 _{paper['why_read']}_\n\n"
        f"🔗 <{paper['url']}|원문 논문 보기>\n"
        f"📝 <{notion_url}|노션에서 정리 보기>"
    )

    resp = requests.post(
        SLACK_WEBHOOK_URL,
        json={"text": text},
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"❌ Slack 에러: {resp.status_code} {resp.text}")
        resp.raise_for_status()

    print("✅ Slack 알림 전송 완료")


# ==================== 메인 실행 ====================

def main():
    print(f"🕐 실행 시각: {datetime.now(timezone(timedelta(hours=9)))}")

    # 1. 오늘의 논문
    paper = get_today_paper()

    # 2. Claude로 정리
    summary = summarize_paper(paper)

    # 3. Notion에 저장
    notion_url = create_notion_page(paper, summary)

    # 4. Slack 알림
    send_slack_notification(paper, notion_url)

    print("🎉 오늘의 논문 처리 완료!")


if __name__ == "__main__":
    main()