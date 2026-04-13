#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF打者データ → batter_data.json 変換スクリプト

使い方:
  python convert_batter.py            # 差分確認のみ（JSONは変更しない）
  python convert_batter.py --update   # batter_data.json を更新する

対象ページ: P3・P4（Aチーム打者データ）
"""

import pdfplumber
import json
import re
import sys
import io
from pathlib import Path

# Windows コンソールの文字化け対策
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent

# Aチーム列の固定順（背番号 → 選手名）
PLAYER_ORDER_A = [
    {"name": "拓斗",   "number": 10},
    {"name": "颯真",   "number": 1},
    {"name": "悠人",   "number": 2},
    {"name": "玄治",   "number": 3},
    {"name": "吉海翔", "number": 4},
    {"name": "俊斗",   "number": 5},
    {"name": "文徳",   "number": 6},
    {"name": "颯人",   "number": 7},
    {"name": "秀矢",   "number": 8},
    {"name": "啓仁",   "number": 9},
    {"name": "奥海杜", "number": 11},
    {"name": "太洋",   "number": 12},
    {"name": "新",     "number": 13},
    {"name": "瞭",     "number": 14},
]

# Bチーム列の固定順（PDF列順: 背番号 10,4,6,8,11,12,13,14,15,16,17,18,19,20,21,22,23）
PLAYER_ORDER_B = [
    {"name": "玄治",   "number": 10},
    {"name": "吉海翔", "number": 4},
    {"name": "文徳",   "number": 6},
    {"name": "秀矢",   "number": 8},
    {"name": "奥海杜", "number": 11},
    {"name": "太洋",   "number": 12},
    {"name": "新",     "number": 13},
    {"name": "瞭",     "number": 14},
    {"name": "拓巳",   "number": 15},
    {"name": "大和",   "number": 16},
    {"name": "翔",     "number": 17},
    {"name": "悠明",   "number": 18},
    {"name": "翔一朗", "number": 19},
    {"name": "奏太",   "number": 20},
    {"name": "壮一郎", "number": 21},
    {"name": "太慈",   "number": 22},
    {"name": "結也",   "number": 23},
]

# 後方互換: デフォルトはAチーム
PLAYER_ORDER = PLAYER_ORDER_A

# PDF上の行ラベル → JSONキー
STAT_LABELS = {
    "打席":   "pa",
    "出塁":   "onBase",
    "得点":   "runs",
    "四死球": "walks",
    "安打":   "hits",
    "三振":   "strikeouts",
}
STAT_KEYS = list(STAT_LABELS.values())

# 大会名の正規化（PDFが略称の場合に対応）
TOURNAMENT_MAP = {
    "あじさい":     "あじさいリーグ",
    "あじさいリーグ": "あじさいリーグ",
    "区大会":       "区大会",
    "OP戦":        "OP戦",
    "さわやか":     "さわやかカップ",
    "さわやかカップ": "さわやかカップ",
    "若獅子":       "若獅子カップ",
}

# 対戦相手名の正規化（PDFと既存JSONで表記揺れがある場合）
OPPONENT_MAP = {
    "百合丘ペッカーズ": "百合ヶ丘ペッカーズ",
}

# ────────────────────────────────────────────
# PDF検索
# ────────────────────────────────────────────
def find_pdf(team: str = 'A') -> Path:
    all_pdfs = sorted(BASE_DIR.glob("*.pdf"), key=lambda p: p.name, reverse=True)
    pdfs = [p for p in all_pdfs if p.name.startswith(team)]
    if not pdfs:
        raise FileNotFoundError(f"{team}チーム用PDFファイル（{team}データ分析★MMDD★.pdf）が見つかりません")
    if len(pdfs) > 1:
        print(f"複数のPDFが見つかりました。最新ファイルを使用します: {pdfs[0].name}")
    return pdfs[0]


# ────────────────────────────────────────────
# ヘッダー解析
# ────────────────────────────────────────────
def parse_game_header(line: str) -> dict | None:
    """
    例: "3月29日A 区大会 茅ヶ崎エンデバーズ 負 6対10"
    """
    # 相手名と勝敗の間にスペースがない場合（例: ヤンキース負）や
    # PDF抽出で勝敗文字の後に相手名の一部が残る場合（例: ヤンキー負ス）に対応
    m = re.match(
        r'(\d+)月(\d+)日([A-Z])\s+(\S+)\s+(.+?)\s*(勝|負|引き分け)(\S*)\s+(\d+対\d+)',
        line
    )
    if not m:
        return None
    month, day, team, tournament, opp_head, result, opp_tail, score = m.groups()
    opponent = (opp_head + opp_tail).strip()
    tournament = TOURNAMENT_MAP.get(tournament, tournament)
    opponent   = OPPONENT_MAP.get(opponent.strip(), opponent.strip())
    return {
        "date":       f"2026-{int(month):02d}-{int(day):02d}",
        "team":       team,
        "tournament": tournament,
        "opponent":   opponent,
        "result":     result,
        "score":      score,
    }


# ────────────────────────────────────────────
# 打席行のパース
# ────────────────────────────────────────────
def parse_pa_row(tokens: list[str], n_players: int = 14) -> tuple[int, list]:
    """
    打席行フォーマット:
      total ー p1_val ー p2_val ー ... (欠場選手は値なしで ー のみ)

    Returns: (team_total, [n_players要素リスト, None=欠場])
    """
    if not tokens:
        return 0, [None] * n_players

    try:
        total = int(tokens[0])
    except (ValueError, IndexError):
        total = 0

    rest = tokens[2:]  # "total ー" の2トークンをスキップ
    players = []
    i = 0
    while len(players) < n_players:
        if i >= len(rest):
            players.append(None)
            continue
        t = rest[i]
        if t == 'ー':
            # 欠場（値なし）
            players.append(None)
            i += 1
        else:
            try:
                players.append(int(t))
                i += 2  # 値 + 後続の ー をスキップ
            except ValueError:
                players.append(None)
                i += 1
    return total, players


# ────────────────────────────────────────────
# 出塁/得点/四死球/安打/三振 行のパース
# ────────────────────────────────────────────
def parse_stat_row(tokens: list[str], absent_mask: list[bool]) -> tuple[int, list]:
    """
    成績行フォーマット:
      total rate% v1 r1% v2 r2% ...  ※欠場選手の列は省略される

    Returns: (team_total, [14要素リスト, None=欠場])
    """
    if not tokens:
        return 0, [None if a else 0 for a in absent_mask]
    try:
        total = int(tokens[0])
    except (ValueError, IndexError):
        total = 0

    # "%"で終わるトークン（率）を除去し、整数値のみ抽出
    # これにより "0 0 0%" のようなPDF抽出の乱れに対応できる
    player_vals = []
    for t in tokens[1:]:          # tokens[0]=total は除外済み
        s = str(t)
        if s.endswith('%'):
            continue              # 率トークンはスキップ
        try:
            player_vals.append(int(s))
        except ValueError:
            pass                  # "ー" などは無視

    result = []
    vi = 0
    for is_absent in absent_mask:
        if is_absent:
            result.append(None)
        else:
            result.append(player_vals[vi] if vi < len(player_vals) else 0)
            vi += 1
    return total, result


# ────────────────────────────────────────────
# 試合オブジェクトの構築
# ────────────────────────────────────────────
def build_game(header: dict, stat_rows: dict, player_order: list = None) -> dict:
    if player_order is None:
        player_order = PLAYER_ORDER_A
    n_players = len(player_order)
    pa_tokens = stat_rows.get("pa", [])
    total_pa, pa_values = parse_pa_row(pa_tokens, n_players)
    absent_mask = [v is None for v in pa_values]

    team_totals = {"pa": total_pa}
    player_data = {"pa": pa_values}

    for key in ["onBase", "runs", "walks", "hits", "strikeouts"]:
        tokens = stat_rows.get(key, [])
        total, values = parse_stat_row(tokens, absent_mask)
        team_totals[key] = total
        player_data[key] = values

    players = []
    for idx, p in enumerate(player_order):
        player = {"name": p["name"], "number": p["number"]}
        for key in STAT_KEYS:
            vals = player_data.get(key, [])
            player[key] = vals[idx] if idx < len(vals) else None
        players.append(player)

    return {**header, "team_totals": team_totals, "players": players}


# ────────────────────────────────────────────
# バリデーション（選手合計 vs チーム合計）
# ────────────────────────────────────────────
def validate_game(game: dict) -> list[str]:
    warnings = []
    for key in STAT_KEYS:
        computed = sum(p[key] for p in game["players"] if p[key] is not None)
        expected = game["team_totals"].get(key, 0)
        if computed != expected:
            warnings.append(f"    {key}: チーム合計={expected}, 選手合計={computed} (差={computed - expected:+d})")
    return warnings


# ────────────────────────────────────────────
# PDFからの抽出
# ────────────────────────────────────────────
def extract_batter_games(pdf_path: Path, team: str = 'A') -> list[dict]:
    # Aチーム: P3+P4 (pages[2:4]) / Bチーム: P2のみ (pages[1:2])
    if team == 'A':
        page_slice = slice(2, 4)
        min_pages  = 4
        player_order = PLAYER_ORDER_A
    else:
        page_slice = slice(1, 2)
        min_pages  = 2
        player_order = PLAYER_ORDER_B

    page_texts = []
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        if total < min_pages:
            raise ValueError(f"PDFのページ数が不足しています（{total}ページ）。")
        for page in pdf.pages[page_slice]:
            text = page.extract_text() or ""
            page_texts.append(text)

    full_text = "\n".join(page_texts)
    lines = [l.strip() for l in full_text.splitlines() if l.strip()]

    # スキップ対象の行パターン（チーム共通 + チーム固有）
    SKIP_PATTERNS_BASE = [
        r'^[A-Z]チーム打者データ',
        r'^日付',
        r'^※',
        r'^全試合累計',
        r'^直近',
        r'^打率',
        r'^\d+\s+\d+\s+\d+',    # 選手番号ヘッダー行
    ]
    SKIP_PATTERNS_A = [
        r'^\d+\s+颯真\s+悠人',  # A選手名ヘッダー行
        r'^拓斗\s+颯真',
    ]
    SKIP_PATTERNS_B = [
        r'^玄治\s+吉',           # B選手名ヘッダー行
    ]
    skip_patterns = SKIP_PATTERNS_BASE + (SKIP_PATTERNS_A if team == 'A' else SKIP_PATTERNS_B)

    games = []
    current_header = None
    stat_rows = {}

    for line in lines:
        # スキップ判定
        if any(re.match(p, line) for p in skip_patterns):
            # 集計区切り（"全試合累計"など）で前の試合を保存
            if "全試合累計" in line or "直近" in line:
                if current_header and stat_rows:
                    games.append(build_game(current_header, stat_rows, player_order))
                    current_header = None
                    stat_rows = {}
            continue

        # 試合ヘッダー判定
        if re.match(r'\d+月\d+日[A-Z]', line):
            if current_header and stat_rows:
                games.append(build_game(current_header, stat_rows, player_order))
            current_header = parse_game_header(line)
            stat_rows = {}
            if current_header is None:
                print(f"  [警告] ヘッダー解析失敗: {line}")
            continue

        # 成績行の判定
        for pdf_label, json_key in STAT_LABELS.items():
            if line.startswith(pdf_label):
                tokens = line.split()[1:]  # ラベルトークンを除く
                stat_rows[json_key] = tokens
                break

    # 最後の試合
    if current_header and stat_rows:
        games.append(build_game(current_header, stat_rows, player_order))

    return games


# ────────────────────────────────────────────
# 比較・レポート
# ────────────────────────────────────────────
def game_id(g: dict) -> str:
    return f"{g['date']}|{g.get('team','')}|{g['tournament']}|{g['opponent']}"


def compare_and_report(pdf_games: list, json_games: list):
    pdf_map  = {game_id(g): g for g in pdf_games}
    json_map = {game_id(g): g for g in json_games}
    pdf_keys  = set(pdf_map)
    json_keys = set(json_map)

    print(f"\n{'='*60}")
    print(f"PDF取得: {len(pdf_games)}試合  /  JSON既存: {len(json_games)}試合")

    only_pdf  = pdf_keys - json_keys
    only_json = json_keys - pdf_keys
    common    = pdf_keys & json_keys

    if only_pdf:
        print(f"\n【JSONに未追加の試合 (PDF → 新規追加候補)】")
        for k in sorted(only_pdf):
            print(f"  + {k}")

    if only_json:
        print(f"\n【PDFに存在しない試合 (JSONのみ)】")
        for k in sorted(only_json):
            print(f"  ? {k}")

    print(f"\n【共通試合の差分チェック ({len(common)}試合)】")
    total_diffs = 0
    for k in sorted(common):
        pg = pdf_map[k]
        jg = json_map[k]
        diffs = []
        for key in STAT_KEYS:
            pv = pg["team_totals"].get(key)
            jv = jg["team_totals"].get(key)
            if pv != jv:
                diffs.append(f"    team_totals.{key}: PDF={pv}  JSON={jv}")
        for pp, jp in zip(pg["players"], jg["players"]):
            name = pp["name"]
            for key in STAT_KEYS:
                pv = pp.get(key)
                jv = jp.get(key)
                if pv != jv:
                    diffs.append(f"    [{name}#{pp['number']}] {key}: PDF={pv}  JSON={jv}")
        if diffs:
            total_diffs += len(diffs)
            print(f"\n  試合: {k}")
            for d in diffs:
                print(d)

    if total_diffs == 0:
        print("  差分なし ✓")
    else:
        print(f"\n  合計 {total_diffs} 件の差分")

    print(f"\n【バリデーション: 選手合計 vs チーム合計 (PDF取得データ)】")
    validation_ok = True
    for g in pdf_games:
        warns = validate_game(g)
        if warns:
            validation_ok = False
            print(f"\n  ⚠ {game_id(g)}")
            for w in warns:
                print(w)
    if validation_ok:
        print("  全試合OK ✓")


# ────────────────────────────────────────────
# メイン
# ────────────────────────────────────────────
def main():
    update_mode = "--update" in sys.argv
    team = 'B' if '--team' in sys.argv and sys.argv[sys.argv.index('--team') + 1] == 'B' else 'A'

    print("PDFを検索中...")
    try:
        pdf_path = find_pdf(team)
        print(f"使用PDF: {pdf_path.name}")
    except FileNotFoundError as e:
        print(f"エラー: {e}")
        sys.exit(1)

    pages_desc = "P2" if team != 'A' else "P3・P4"
    print(f"打者データを抽出中 ({pages_desc})...")
    try:
        pdf_games = extract_batter_games(pdf_path, team)
    except Exception as e:
        print(f"抽出エラー: {e}")
        sys.exit(1)
    print(f"{len(pdf_games)} 試合を取得しました")

    batter_path = BASE_DIR / "batter_data.json"
    if batter_path.exists():
        with open(batter_path, encoding="utf-8") as f:
            json_games = json.load(f)
    else:
        json_games = []
        print("batter_data.json が存在しないため新規作成します")

    compare_and_report(pdf_games, json_games)

    if update_mode:
        # PDF優先でマージ（PDFにない試合はJSONから保持）
        merged = {game_id(g): g for g in json_games}
        for g in pdf_games:
            merged[game_id(g)] = g

        sorted_games = sorted(merged.values(), key=lambda g: g["date"], reverse=True)

        with open(batter_path, "w", encoding="utf-8") as f:
            json.dump(sorted_games, f, ensure_ascii=False, indent=2)
        print(f"\n✓ batter_data.json を更新しました ({len(sorted_games)} 試合)")
    else:
        team_arg = f" --team {team}" if team != 'A' else ""
        print(f"\n※ 更新するには: python convert_batter.py{team_arg} --update")


if __name__ == "__main__":
    main()
