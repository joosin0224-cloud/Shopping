"""쇼핑 리스트 앱 (Streamlit)"""

import html
import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st

DATA_FILE = Path(__file__).parent / "shopping_list.json"
NAME_MAX_LEN = 50
QTY_MIN, QTY_MAX = 1, 999


# ===== 데이터 처리 함수 =====
# 모든 변경은 "잠금 → 파일에서 최신 목록 읽기 → 수정 → 저장" 순서로 처리한다.
# (여러 탭/세션이 동시에 써도 서로의 변경을 덮어쓰지 않게 하기 위함)

@st.cache_resource
def _file_lock() -> threading.RLock:
    """모든 세션이 공유하는 파일 잠금"""
    return threading.RLock()


def _is_valid_item(item: object) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("id"), str) and bool(item["id"])
        and isinstance(item.get("name"), str) and bool(item["name"].strip())
        and type(item.get("quantity")) is int and QTY_MIN <= item["quantity"] <= QTY_MAX
        and type(item.get("checked")) is bool
    )


def _quarantine_corrupt_file() -> str:
    """손상된 파일을 덮어쓰지 않도록 백업 파일로 옮기고, 백업 파일 이름을 반환"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = DATA_FILE.with_name(f"{DATA_FILE.stem}.corrupt-{stamp}.json")
    n = 1
    while backup.exists():  # 같은 초에 여러 번 손상돼도 이전 백업을 덮어쓰지 않게
        n += 1
        backup = DATA_FILE.with_name(f"{DATA_FILE.stem}.corrupt-{stamp}-{n}.json")
    os.replace(DATA_FILE, backup)
    return backup.name


def load_items() -> list[dict]:
    """JSON 파일에서 목록 로드. 파일이 없거나 손상되면 빈 목록 반환."""
    with _file_lock():
        if not DATA_FILE.exists():
            return []
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, list) or not all(_is_valid_item(i) for i in data):
                raise ValueError("잘못된 데이터 형식")
            return data
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            st.session_state["load_error"] = _quarantine_corrupt_file()
            return []


def save_items(items: list[dict]) -> None:
    """목록을 JSON 파일에 저장 (임시 파일에 쓴 뒤 교체해서 중간에 깨지지 않게)"""
    with _file_lock():
        tmp = DATA_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, DATA_FILE)


def _modify(change) -> bool:
    """최신 목록에 change(items)를 적용하고 저장. change가 False를 반환하면 저장하지 않음."""
    with _file_lock():
        items = load_items()
        changed = change(items) is not False
        if changed:
            save_items(items)
        st.session_state["items"] = items
        return changed


def validate(name: str, quantity: int) -> str | None:
    """입력값 검증. 문제가 있으면 오류 메시지, 없으면 None."""
    if not name:
        return "아이템 이름을 입력해 주세요."
    if len(name) > NAME_MAX_LEN:
        return f"아이템 이름은 {NAME_MAX_LEN}자 이하로 입력해 주세요. (현재 {len(name)}자)"
    if not QTY_MIN <= quantity <= QTY_MAX:
        return f"수량은 {QTY_MIN}~{QTY_MAX} 사이로 입력해 주세요."
    return None


def _find(items: list[dict], item_id: str) -> dict | None:
    return next((i for i in items if i["id"] == item_id), None)


def add_item(name: str, quantity: int) -> None:
    """아이템 생성 후 추가"""
    _modify(lambda items: items.append({
        "id": str(uuid.uuid4()),
        "name": name,
        "quantity": quantity,
        "checked": False,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }))


def update_item(item_id: str, name: str, quantity: int) -> bool:
    """아이템 수정. 아이템이 이미 없으면 False."""
    def change(items):
        item = _find(items, item_id)
        if not item:
            return False
        item["name"], item["quantity"] = name, quantity
    return _modify(change)


def delete_item(item_id: str) -> None:
    """아이템 삭제"""
    def change(items):
        items[:] = [i for i in items if i["id"] != item_id]
    _modify(change)
    if st.session_state["editing_id"] == item_id:
        st.session_state["editing_id"] = None


def toggle_item(item_id: str, checked: bool) -> None:
    """체크 상태를 checked로 설정 (토글 대신 목표 값을 지정해 탭 간 충돌 방지)"""
    def change(items):
        item = _find(items, item_id)
        if not item or item["checked"] == checked:
            return False
        item["checked"] = checked
    _modify(change)


def clear_checked() -> None:
    """완료 항목 일괄 삭제"""
    def change(items):
        items[:] = [i for i in items if not i["checked"]]
    _modify(change)


# ===== 상태 초기화 =====

def init_state() -> None:
    # 매 실행마다 파일에서 다시 읽어 다른 탭의 변경도 반영
    st.session_state["items"] = load_items()
    ids = {i["id"] for i in st.session_state["items"]}
    if st.session_state.get("editing_id") not in ids:
        st.session_state["editing_id"] = None


def md_escape(text: str) -> str:
    """st.toast 등 마크다운으로 해석되는 곳에 사용자 입력을 그대로 보이게"""
    return re.sub(r"([\\`*_\[\]{}()#+\-.!|~<>:$&])", r"\\\1", text)


def flash(message: str) -> None:
    """다음 실행(rerun) 후 보여줄 toast 메시지 예약"""
    st.session_state["flash"] = message

# ===== 스타일 =====

CSS = """
<style>
.block-container { max-width: 720px; padding-top: 2.5rem; }

/* 진행 현황 카드 */
.st-key-progress_card {
    background: rgba(76, 140, 110, 0.08);
    border: 1px solid rgba(76, 140, 110, 0.25);
    border-radius: 14px;
    padding: 1rem 1.25rem 0.75rem;
}
.progress-text { font-size: 0.95rem; opacity: 0.85; margin: 0; }
.progress-text b { font-size: 1.15rem; }
.done-banner { font-weight: 600; color: #3f7d60; margin: 0 0 0.25rem; }

/* 추가 폼 */
[data-testid="stForm"] {
    border-radius: 14px;
    border: 1px solid rgba(128, 128, 128, 0.25);
    padding: 1rem;
}
[class*="st-key-editrow_"] [data-testid="stForm"] { border: none; padding: 0; }

/* 아이템 행 */
[class*="st-key-row_"], [class*="st-key-editrow_"] {
    border-bottom: 1px solid rgba(128, 128, 128, 0.15);
    padding: 0.35rem 0.5rem;
    border-radius: 10px;
}
[class*="st-key-row_"]:hover { background: rgba(128, 128, 128, 0.06); }
[class*="st-key-editrow_"] {
    background: rgba(255, 196, 0, 0.12);
    border: 1px solid rgba(255, 196, 0, 0.45);
}
.item-name { font-size: 1.02rem; margin: 0; word-break: break-all; line-height: 1.6; }
.item-name.checked { text-decoration: line-through; opacity: 0.45; }
.qty-badge {
    display: inline-block;
    font-size: 0.75rem;
    font-weight: 600;
    padding: 0.05rem 0.5rem;
    margin-left: 0.4rem;
    border-radius: 999px;
    background: rgba(76, 140, 110, 0.15);
    color: #3f7d60;
    vertical-align: middle;
}
.item-name.checked .qty-badge { background: rgba(128, 128, 128, 0.15); color: inherit; }

/* 체크박스와 이름 세로 정렬 */
[class*="st-key-row_"] [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
[class*="st-key-row_"] [data-testid="stMarkdownContainer"] p { margin-bottom: 0; }
[class*="st-key-row_"] [data-testid="stCheckbox"] label { margin-bottom: 0; padding: 0; min-height: 0; }
[class*="st-key-row_"] [data-testid="stElementContainer"] { margin: 0; }

/* 버튼 글자 줄바꿈 방지 */
button p { white-space: nowrap; }

/* 행 안의 버튼은 작고 담백하게 */
[class*="st-key-row_"] button, [class*="st-key-editrow_"] button {
    padding: 0.15rem 0.5rem;
    min-height: 2.1rem;
}

/* 좁은 화면에서도 행이 세로로 쪼개지지 않게 */
[class*="st-key-row_"] [data-testid="stHorizontalBlock"],
[class*="st-key-editrow_"] [data-testid="stHorizontalBlock"],
.st-key-add_row [data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    gap: 0.4rem;
}
[class*="st-key-row_"] [data-testid="stColumn"],
[class*="st-key-editrow_"] [data-testid="stColumn"],
.st-key-add_row [data-testid="stColumn"] {
    min-width: 0 !important;
}

@media (max-width: 640px) {
    .st-key-add_row button { padding: 0.25rem 0.4rem; }
}

.empty-state {
    text-align: center;
    padding: 2rem 1rem;
    opacity: 0.6;
    border: 1px dashed rgba(128, 128, 128, 0.35);
    border-radius: 14px;
}
</style>
"""


# ===== 화면 영역 =====
# 버튼 동작은 모두 on_click/on_change 콜백으로 처리한다.
# 콜백은 화면을 그리기 전에 실행되므로 st.rerun() 없이 한 번만 그려서 빠르다.

def render_progress() -> None:
    """진행 현황 영역"""
    items = st.session_state["items"]
    total = len(items)
    done = sum(i["checked"] for i in items)
    with st.container(key="progress_card"):
        st.markdown(
            f'<p class="progress-text">전체 <b>{total}</b>개 중 <b>{done}</b>개 완료</p>',
            unsafe_allow_html=True,
        )
        st.progress(done / total if total else 0.0)
        if total and done == total:
            st.markdown('<p class="done-banner">🎉 장보기 완료!</p>', unsafe_allow_html=True)


def _on_add() -> None:
    name = st.session_state["add_name"].strip()
    quantity = int(st.session_state["add_qty"])
    error = validate(name, quantity)
    if error:
        st.session_state["add_error"] = error
        return
    add_item(name, quantity)
    flash(f"'{md_escape(name)}' 추가됨")


def render_add_form() -> None:
    """아이템 추가 영역"""
    # max_chars / min_value / max_value를 쓰지 않는다: 위젯이 범위 밖 입력을 조용히
    # 버리거나 이전 값으로 바꿔 버리므로, 값을 그대로 받아 validate()로 안내한다.
    with st.form("add_form", clear_on_submit=True):
        with st.container(key="add_row"):
            c1, c2, c3 = st.columns([4, 2, 1.6], vertical_alignment="bottom")
            c1.text_input("아이템 이름", placeholder="예: 우유", key="add_name")
            c2.number_input("수량", value=1, step=1, key="add_qty")
            c3.form_submit_button("추가", type="primary", width="stretch", on_click=_on_add)
    if error := st.session_state.pop("add_error", None):
        st.warning(error)


def _on_check(item_id: str) -> None:
    toggle_item(item_id, st.session_state[f"check_{item_id}"])


def _on_edit(item_id: str) -> None:
    st.session_state["editing_id"] = item_id


def _on_save(item_id: str) -> None:
    name = st.session_state[f"edit_name_{item_id}"].strip()
    quantity = int(st.session_state[f"edit_qty_{item_id}"])
    error = validate(name, quantity)
    if error:
        st.session_state["edit_error"] = error  # 편집 모드 유지
        return
    st.session_state["editing_id"] = None
    if update_item(item_id, name, quantity):
        flash("수정됨")
    else:
        flash("다른 곳에서 이미 삭제된 아이템이라 수정하지 못했습니다")


def _on_cancel() -> None:
    st.session_state["editing_id"] = None


def _on_delete(item_id: str, name: str) -> None:
    delete_item(item_id)
    flash(f"'{md_escape(name)}' 삭제됨")


def _on_clear() -> None:
    clear_checked()
    flash("완료 항목을 삭제했습니다")


def render_edit_row(item: dict) -> None:
    item_id = item["id"]
    with st.container(key=f"editrow_{item_id}"):
        # form으로 감싸서 Enter 키로 저장되게 함 (첫 번째 제출 버튼 = 저장)
        with st.form(key=f"editform_{item_id}", border=False):
            cols = st.columns([4, 2, 1.3, 1.3], vertical_alignment="center")
            cols[0].text_input(
                "이름", value=item["name"], key=f"edit_name_{item_id}",
                label_visibility="collapsed",
            )
            cols[1].number_input(
                "수량", value=item["quantity"], step=1,
                key=f"edit_qty_{item_id}", label_visibility="collapsed",
            )
            cols[2].form_submit_button(
                "저장", key=f"save_{item_id}", type="primary", width="stretch",
                on_click=_on_save, args=(item_id,))
            cols[3].form_submit_button(
                "취소", key=f"cancel_{item_id}", width="stretch", on_click=_on_cancel)
    if error := st.session_state.pop("edit_error", None):
        st.warning(error)


def render_item_row(item: dict) -> None:
    item_id = item["id"]
    if st.session_state["editing_id"] == item_id:
        render_edit_row(item)
        return

    is_editing_other = st.session_state["editing_id"] is not None
    check_key = f"check_{item_id}"
    # 위젯 상태를 매번 파일 기준 값으로 맞춤 (다른 탭에서 바뀐 경우 대비)
    st.session_state[check_key] = item["checked"]

    with st.container(key=f"row_{item_id}"):
        cols = st.columns([0.6, 6, 0.9, 0.9], vertical_alignment="center")
        cols[0].checkbox(
            "구매 완료", key=check_key, on_change=_on_check, args=(item_id,),
            label_visibility="collapsed",
        )
        css_class = "item-name checked" if item["checked"] else "item-name"
        cols[1].markdown(
            f'<p class="{css_class}">{html.escape(item["name"])}'
            f'<span class="qty-badge">x{item["quantity"]}</span></p>',
            unsafe_allow_html=True,
        )
        cols[2].button(
            "✏️", key=f"edit_{item_id}", type="tertiary",
            disabled=is_editing_other, on_click=_on_edit, args=(item_id,),
            help="편집 중인 항목을 먼저 저장하거나 취소하세요" if is_editing_other else "수정",
        )
        cols[3].button(
            "🗑️", key=f"del_{item_id}", help="삭제", type="tertiary",
            on_click=_on_delete, args=(item_id, item["name"]),
        )


def render_item_list() -> None:
    """아이템 목록 영역"""
    items = st.session_state["items"]
    if not items:
        st.markdown(
            '<div class="empty-state">🧺<br>쇼핑 리스트가 비어 있습니다</div>',
            unsafe_allow_html=True,
        )
        return
    for item in items:
        render_item_row(item)


def render_clear_button() -> None:
    """일괄 삭제 영역"""
    has_checked = any(i["checked"] for i in st.session_state["items"])
    st.write("")
    _, right = st.columns([2, 1])
    right.button("완료 항목 모두 삭제", disabled=not has_checked, width="stretch",
                 on_click=_on_clear)


def main() -> None:
    st.set_page_config(page_title="쇼핑 리스트", page_icon="🛒", layout="centered")
    st.markdown(CSS, unsafe_allow_html=True)
    init_state()

    st.title("🛒 쇼핑 리스트")

    if backup := st.session_state.pop("load_error", None):
        st.warning(
            f"저장 파일이 손상되어 빈 목록으로 시작합니다. "
            f"원래 파일은 `{backup}`로 보관했습니다."
        )
    if msg := st.session_state.pop("flash", None):
        st.toast(msg)

    render_progress()
    st.write("")
    render_add_form()
    st.divider()
    render_item_list()
    render_clear_button()


if __name__ == "__main__":
    main()
