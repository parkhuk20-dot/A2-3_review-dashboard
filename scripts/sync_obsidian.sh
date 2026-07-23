#!/usr/bin/env bash
# docs/PLAN.md 를 Obsidian 볼트 노트로 동기화한다.
# 계획·진행상황의 단일 진실 소스는 docs/PLAN.md 이고, 이 스크립트가 볼트로 복사한다.
#
#   ./scripts/sync_obsidian.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO_ROOT/docs/PLAN.md"
VAULT_NOTE="/Users/sejin/Desktop/Obsidian/AI-Wiki/05 Projects/Bootcamp/고객 리뷰 감정 분석 대시보드.md"

if [ ! -f "$SRC" ]; then
  echo "원본 문서를 찾을 수 없습니다: $SRC" >&2
  exit 1
fi

mkdir -p "$(dirname "$VAULT_NOTE")"

{
  echo "Tags: #project #bootcamp #python #cli #ai #sentiment #dashboard"
  echo
  echo "> 이 노트는 저장소 \`docs/PLAN.md\` 에서 자동 동기화됩니다. (마지막 동기화: $(date '+%Y-%m-%d %H:%M'))"
  echo "> 저장소: \`~/Desktop/codyssey/A2-1_review-dashboard\`"
  echo
  cat "$SRC"
} > "$VAULT_NOTE"

echo "Obsidian 동기화 완료 → $VAULT_NOTE"
