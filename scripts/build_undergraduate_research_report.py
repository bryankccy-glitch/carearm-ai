"""Build the NSTC C802-style CareArm undergraduate research proposal."""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "undergraduate_research" / "CareArm_AI_大專學生研究計畫_C802.docx"
FIG_STRATEGY = ROOT / "results" / "exp1200" / "figures" / "strategy_comparison.png"
FIG_ABLATION = ROOT / "results" / "exp1200" / "figures" / "ablation.png"
FIG_CAMERA = ROOT / "results" / "demo_camera.png"

BLUE = "17365D"
PALE_BLUE = "EAF2F8"
PALE_GRAY = "F5F7F9"
GRID = "D9D9D9"


def set_cell_shading(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=100, start=100, bottom=100, end=100) -> None:
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for tag, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{tag}"))
        if node is None:
            node = OxmlElement(f"w:{tag}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_table_borders(table, color=GRID, size="6") -> None:
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = borders.find(qn(f"w:{edge}"))
        if el is None:
            el = OxmlElement(f"w:{edge}")
            borders.append(el)
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), size)
        el.set(qn("w:color"), color)


def set_repeat_table_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)


def style_run(run, *, size=12, bold=False, color="000000", east="BiauKai") -> None:
    run.bold = bold
    run.font.size = Pt(size)
    run.font.color.rgb = RGBColor.from_string(color)
    run.font.name = "Times New Roman"
    r_pr = run._element.get_or_add_rPr()
    r_fonts = r_pr.rFonts
    if r_fonts is None:
        r_fonts = OxmlElement("w:rFonts")
        r_pr.insert(0, r_fonts)
    r_fonts.set(qn("w:ascii"), "Times New Roman")
    r_fonts.set(qn("w:hAnsi"), "Times New Roman")
    r_fonts.set(qn("w:eastAsia"), east)


def add_text(doc, text: str, *, bold_lead: str | None = None, indent=True, after=3) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.space_after = Pt(after)
    if indent:
        p.paragraph_format.first_line_indent = Cm(0.85)
    if bold_lead and text.startswith(bold_lead):
        r1 = p.add_run(bold_lead)
        style_run(r1, bold=True)
        r2 = p.add_run(text[len(bold_lead):])
        style_run(r2)
    else:
        r = p.add_run(text)
        style_run(r)


def add_bullet(doc, text: str, level=0) -> None:
    p = doc.add_paragraph(style="List Bullet" if level == 0 else "List Bullet 2")
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.space_after = Pt(2)
    for r in p.runs:
        style_run(r)
    if not p.runs:
        style_run(p.add_run(text))
    else:
        p.runs[0].text = text


def add_numbered(doc, number: int, text: str) -> None:
    """Add a stable, manually numbered item so each section can restart at 1."""
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.85)
    p.paragraph_format.first_line_indent = Cm(-0.85)
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.space_after = Pt(2)
    style_run(p.add_run(f"{number}.  {text}"))


def add_heading(doc, text: str, level=1) -> None:
    p = doc.add_paragraph(style=f"Heading {level}")
    p.paragraph_format.keep_with_next = True
    p.paragraph_format.space_before = Pt(8 if level == 1 else 5)
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(text)
    style_run(r, size=15 if level == 1 else 13, bold=True)


def add_caption(doc, text: str) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(5)
    style_run(p.add_run(text), size=10)


def add_table(doc, headers, rows, widths=None, font_size=10.5):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    set_table_borders(table)
    set_repeat_table_header(table.rows[0])
    for i, header in enumerate(headers):
        cell = table.rows[0].cells[i]
        set_cell_shading(cell, BLUE)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        set_cell_margins(cell)
        if widths:
            cell.width = Cm(widths[i])
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(0)
        style_run(p.add_run(str(header)), size=font_size, bold=True, color="FFFFFF")
    for ridx, row in enumerate(rows):
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cell = cells[i]
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            set_cell_margins(cell)
            if widths:
                cell.width = Cm(widths[i])
            if ridx % 2 == 1:
                set_cell_shading(cell, PALE_BLUE)
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER if i == 0 or len(str(value)) < 18 else WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.line_spacing = 1.0
            p.paragraph_format.space_after = Pt(0)
            style_run(p.add_run(str(value)), size=font_size)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)
    return table


def add_page_number(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    fld_char1 = OxmlElement("w:fldChar")
    fld_char1.set(qn("w:fldCharType"), "begin")
    instr_text = OxmlElement("w:instrText")
    instr_text.set(qn("xml:space"), "preserve")
    instr_text.text = " PAGE "
    fld_char2 = OxmlElement("w:fldChar")
    fld_char2.set(qn("w:fldCharType"), "end")
    run._r.extend([fld_char1, instr_text, fld_char2])
    style_run(run, size=10)


def new_page(doc) -> None:
    doc.add_page_break()


def build() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    sec = doc.sections[0]
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.top_margin = Cm(2.2)
    sec.bottom_margin = Cm(2.0)
    sec.left_margin = Cm(2.35)
    sec.right_margin = Cm(2.35)
    sec.header_distance = Cm(0.8)
    sec.footer_distance = Cm(0.9)
    add_page_number(sec.footer.paragraphs[0])

    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "BiauKai")
    for style_name in ("Title", "Heading 1", "Heading 2"):
        style = doc.styles[style_name]
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.font.name = "Times New Roman"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "BiauKai")

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    style_run(p.add_run("表 C802"), size=11, bold=True)
    # Do not use Word's built-in Title style here: some LibreOffice/Word
    # installations render that style with a blue bottom border.
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Pt(8)
    title.paragraph_format.space_after = Pt(8)
    style_run(title.add_run("具不確定性感知與人工覆核機制之長照取藥機械手臂研究"), size=18, bold=True)
    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.space_after = Pt(12)
    style_run(subtitle.add_run("Uncertainty Aware Robotic Medication Retrieval with Human Review for Elderly Care"), size=11)

    add_table(
        doc,
        ["欄位", "內容"],
        [
            ["申請人", "程陳宥"],
            ["就讀學校及系所", "＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿"],
            ["年級", "＿＿＿＿＿＿"],
            ["指導教授", "＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿"],
            ["擬申請年度", "116 年度"],
            ["研究期間", "116 年 7 月 1 日至 117 年 2 月 29 日，共 8 個月"],
        ],
        widths=[4.2, 11.7],
        font_size=11,
    )

    add_heading(doc, "一 摘要", 1)
    add_text(doc, "本研究擬建置一套面向長照情境的多模態取藥機械手臂系統，使 Franka Panda 機械手臂能依據使用者語音、藥盒視覺資訊與服藥排程，自動判斷應取藥盒並完成夾取、搬運與放置。研究核心並非追求單一分類器的最高準確率，而是探討當語音辨識、影像偵測或排程資訊存在不確定性時，如何以硬性安全規則、校正後風險估計及人工覆核形成可解釋的兩層安全決策。系統另以手腕 RGB-D 相機觀察藥杯，判斷藥丸是否仍在杯內、是否部分取走或疑似掉落，但不宣稱能證明真人已吞服。")
    add_text(doc, "研究將在 MuJoCo 中建立 Panda、開口藥杯、獨立藥丸、俯視相機及手腕相機，並比較純視覺、規則式多模態、固定信心門檻、校正風險與 conformal 風險控制等策略。主要評估指標包含正確自動完成率、錯盒率、自動執行中的錯誤率、人工確認率、危險動作攔截率、語音字錯誤率、視覺定位誤差及藥杯空杯判讀正確率。前期 1,200 情境模擬顯示，兩層安全策略可將錯盒率由純視覺的 27.5% 降至 0.3%，並維持 39.2% 的自動完成率；惟目前錯誤樣本少、真實語音與實體 RGB-D 相機尚未驗證。本計畫將以壓力情境、匿名真人語音、多次隨機種子及真實相機試驗補強證據，並以 bootstrap 信賴區間與 McNemar 配對檢定呈現結果。")
    add_text(doc, "關鍵詞：長照機器人、多模態感知、不確定性校正、人工覆核、Conformal Risk Control、RGB-D 服藥驗證", indent=False, after=0)

    new_page(doc)
    add_heading(doc, "二 研究動機與研究問題", 1)
    add_heading(doc, "2.1 研究背景與重要性", 2)
    add_text(doc, "世界衛生組織將用藥相關傷害列為全球病人安全的重要議題，並指出問題不只來自個人操作，也涉及藥品、照護人員與整體系統流程[1]。長者可能同時面臨多重用藥、記憶負荷、視聽能力下降及照護人力不足，因此自動提醒或配送藥物具有實際需求。然而，機械手臂若在聽錯時段、看錯藥盒或無法判斷重複取藥時仍執行，錯誤會直接轉化為物理動作；只展示一次成功夾取，不能證明系統在不確定情況下安全。")
    add_text(doc, "近年的藥物管理機器人研究指出，此類系統可能提升依從性、降低照護負擔，但仍須處理技術故障、隱私、人機接受度與長期有效性等問題[2]。因此，本研究不把機器人定位為臨床決策者，而是研究一個可追蹤、可拒絕執行、可交由人員覆核的取藥流程。藥物種類、劑量與處方皆由外部既定資料提供；系統只處理指定藥杯的辨識、搬運及狀態紀錄。")
    add_heading(doc, "2.2 現有系統的問題", 2)
    add_bullet(doc, "單一信心門檻常把未校正的分類分數誤當成真實正確機率，造成過度自信或過度保守。")
    add_bullet(doc, "視覺正確不代表行為合理；即使辨識到正確藥盒，若服藥時間不符或當日已取過，仍不應自動執行。")
    add_bullet(doc, "人工介入若沒有明確觸發條件，容易變成每次都要確認，失去自動化價值；若允許人工覆蓋硬性安全規則，又會破壞安全邊界。")
    add_bullet(doc, "模擬研究容易偷讀物體真實座標或只看成功動畫。本研究需明確分離感測輸入、決策資訊與只供評估的 ground truth。")
    add_bullet(doc, "藥盒送達不等於已服藥；手腕相機最多能確認藥丸是否仍在杯內，不能推論人體是否吞服。")
    add_heading(doc, "2.3 研究目的", 2)
    add_numbered(doc, 1, "建立可重現的語音、視覺、排程與 Panda 物理執行整合平台。")
    add_numbered(doc, 2, "建立不可由低風險分數覆蓋的硬性安全規則，並比較不同不確定性處理策略。")
    add_numbered(doc, 3, "導入校正風險與 conformal 風險控制，使自動執行與人工確認的取捨可量化。")
    add_numbered(doc, 4, "以 RGB-D 手腕視覺驗證杯內藥丸狀態，建立送達、取走、部分取走、疑似掉落與無法確認的流程紀錄。")
    add_numbered(doc, 5, "以統計檢定、信賴區間、消融實驗與失敗案例分析，提供可審查且不誇大的研究證據。")

    new_page(doc)
    add_heading(doc, "2.4 研究問題與假設", 2)
    add_table(
        doc,
        ["編號", "研究問題", "對應假設"],
        [
            ["RQ1", "融合語音、視覺與排程的兩層安全架構，能否比純視覺策略降低錯盒與危險動作？", "H1：規則與排程檢查可顯著降低錯盒率並提高危險攔截率。"],
            ["RQ2", "校正後風險策略能否在相近錯誤率下，比固定 0.8 信心門檻保留更多自動完成？", "H2：校正風險策略的自動完成率較高，執行中錯誤率不劣於固定門檻。"],
            ["RQ3", "Conformal 風險控制在獨立校正集與壓力情境下，能否提供可檢驗的風險上界？", "H3：在交換性假設近似成立時，策略 E 的平均執行風險不超過預設 alpha。"],
            ["RQ4", "手腕 RGB-D 是否能在光線變化與藥丸接觸情況下，可靠判斷藥杯是否為空？", "H4：空杯二元判讀優於精確顆數估計，且可在不確定時輸出 UNVERIFIED。"],
            ["RQ5", "真實中文與台語夾雜語音會如何影響意圖辨識、風險估計與人工確認率？", "H5：真實語音的 CER 與確認率高於文字雜訊模擬，時段詞錯誤是主要風險來源。"],
        ],
        widths=[1.3, 8.2, 6.4],
        font_size=10,
    )
    add_heading(doc, "2.5 研究範圍與安全界線", 2)
    add_text(doc, "研究對象是模擬中的藥杯搬運與感測決策，不涉及處方生成、劑量建議或真實病人治療。所有不安全基線只在 MuJoCo 中執行。真人語音若進行收錄，僅包含固定指令句，不蒐集姓名、病史、藥名或其他健康資料，並於取得同意及完成校內研究倫理判定後執行。實體測試若有，使用無藥性的模型藥丸與空藥杯，手臂速度、工作空間與緊急停止由指導教授及實驗室規範審核。")
    add_text(doc, "本研究所稱「已取完」僅表示視覺上未再看到預期藥丸；介面中的「已服用」是流程狀態，不構成醫療證明。系統發現部分取走、疑似掉落、深度資料不足或語音歧義時，應停止或要求人工確認，而不是自行猜測。")

    new_page(doc)
    add_heading(doc, "三 文獻回顧與探討", 1)
    add_heading(doc, "3.1 長照用藥安全與機器人輔助", 2)
    add_text(doc, "WHO 的 Medication Without Harm 架構將病人、專業人員、藥品以及系統流程共同視為降低用藥傷害的關鍵[1]。這個觀點提醒我們，取藥機器人的問題不只是夾取精度，而是整個資訊鏈是否能在錯誤傳入物理動作前攔截。Maleki 等人針對居家與長照藥物管理機器人的系統性回顧顯示，既有研究雖呈現依從性與照護協調的潛力，但納入研究數仍少，且存在技術、隱私與使用者接受度限制[2]。因此，本計畫把錯誤拒絕、人工覆核與可追溯紀錄納入主要研究指標。")
    add_heading(doc, "3.2 語音理解與多模態意圖", 2)
    add_text(doc, "Whisper 以大規模多語語音資料訓練，具有跨語言與雜訊環境的泛化能力[3]，但通用 ASR 的轉錄仍可能遺失關鍵時段詞。例如「中午」若被誤聽，後續分類器可能根據當前排程推定錯誤時段。Jain 與 Argall 指出，輔助型共享自主系統須對使用者意圖進行機率式推論，而非假設命令完全正確[4]。本研究因此同時使用意圖分類器、關鍵字時段抽取與排程一致性，並把兩者不一致視為風險訊號。")
    add_heading(doc, "3.3 信心校正與選擇性自動化", 2)
    add_text(doc, "分類器輸出的最大機率不必然等於實際正確率。Guo 等人說明溫度縮放可用少量校正資料改善機率可靠度[5]。在安全任務中，校正的目的不是讓分數看起來更高，而是讓「80%」接近長期約八成正確，才能設計執行、確認與停止門檻。本研究分別校正語音意圖與視覺顏色信心，再以 logistic regression 統整為任務錯誤風險。")
    add_text(doc, "Conformal Risk Control 進一步使用獨立校正集，在交換性等假設下控制單調損失函數的期望值[6]。此方法適合研究「要自動完成多少任務，才能讓錯誤風險仍低於 alpha」的選擇性執行問題。不過，理論保證依賴資料分佈與樣本數；若錯誤樣本極少或部署分佈改變，結果必須搭配壓力測試與信賴區間，而不能只引用一個門檻。")

    new_page(doc)
    add_heading(doc, "3.4 視覺定位與物理模擬", 2)
    add_text(doc, "本研究的俯視視覺使用 HSV 分割、輪廓幾何、識別黑點與相機射線和平面交點求世界座標。這種設計比端到端深度網路容易解釋，也可直接檢查顏色、識別碼與唯一性規則。物理驗證則採 MuJoCo；Todorov 等人提出的 MuJoCo 著重接觸動力學與控制研究[7]，適合以相同情境重複比較不同決策策略。Franka Panda 為七自由度協作型機械手臂，具有關節扭矩感測能力[8]；但本計畫目前只在模擬模型中使用，不能把模擬成功率當成實機安全證明。")
    add_heading(doc, "3.5 RGB-D 手腕驗證與研究缺口", 2)
    add_text(doc, "傳統 RGB 前後差分容易把陰影、反光或相機位姿偏移誤認為藥丸變化。CareArm 前期系統因此加入手腕深度影像，把像素還原成三維點，依藥杯內底高度與格子範圍計算杯內凸起物件；若標籤、杯緣或深度有效率不足，輸出 UNVERIFIED，而非硬猜。此設計仍有藥丸接觸造成群聚、模擬深度雜訊過於理想等限制，需要真實相機驗證。")
    add_text(doc, "綜合文獻可見，藥物管理機器人、人類意圖推論、信心校正與風險控制各自已有研究，但較少工作把語音、影像、排程、物理執行、服藥後觀察與人工覆核放入同一套可重現實驗。本研究的貢獻在於建立端到端且具有資訊邊界的評估框架：感知模組不能讀取模擬器答案；硬性規則不受風險模型覆蓋；人工確認只處理可確認的不確定性；所有成功都以實際物理狀態驗證。")
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.add_run().add_picture(str(FIG_CAMERA), width=Inches(3.5))
    add_caption(doc, "圖 1 前期 MuJoCo 俯視相機與藥盒辨識結果")

    new_page(doc)
    add_heading(doc, "四 研究方法及步驟", 1)
    add_heading(doc, "4.1 系統架構", 2)
    add_text(doc, "系統以「語音與排程理解、俯視視覺定位、兩層安全決策、Panda 物理執行、手腕 RGB-D 驗證、事件與人工覆核」六個模組串接。所有 MuJoCo 與 OpenGL 操作由單一 worker 執行緒管理；網頁 API 只送出工作並讀取狀態，避免跨執行緒渲染。")
    add_table(
        doc,
        ["輸入", "感知與規則", "風險決策", "物理動作", "驗證與紀錄"],
        [["語音或文字\n模擬時鐘\n相機 RGB-D", "意圖分類\n時段抽取\n藥盒偵測\n排程檢查", "Layer 1\n硬性限制\n\nLayer 2\n校正風險", "上方接近\n下降夾取\n抬升搬運\n放置返回", "物理位置\n杯內計數\n人工覆核\nSQLite 事件"]],
        widths=[3.0, 3.4, 3.2, 3.3, 3.2],
        font_size=9.5,
    )
    add_caption(doc, "圖 2 CareArm 端到端資料流")
    add_heading(doc, "4.2 兩層安全決策", 2)
    add_text(doc, "第一層為不可覆蓋的硬性規則，包括未要求取藥、排程衝突、資訊不足、重複取藥、找不到目標、識別碼不符及多個同樣目標。任一規則失敗即停止或要求澄清，不計算風險。第二層只處理第一層通過的案例，依語音信心、視覺信心、排程距離等特徵估計任務錯誤風險；低風險自動執行、中間區間請照護者確認、高風險停止。人工確認不得覆蓋第一層。")
    add_heading(doc, "4.3 感知與動作", 2)
    add_bullet(doc, "語音：以 Whisper 或瀏覽器 ASR 取得文字，再用字元 n-gram logistic regression 分類意圖；關鍵字規則獨立抽取早、中、晚。")
    add_bullet(doc, "視覺：手臂先移出俯視相機視野；HSV 找標籤，黑點數作為盒子識別碼；相機幾何將像素轉為世界座標。")
    add_bullet(doc, "抓取：依偵測座標執行接近、下降、閉合、抬升、搬運、放置與返回。成功條件是正確盒進入目標區且其他盒未被明顯撞動。")
    add_bullet(doc, "服藥後觀察：手腕 RGB-D 估計杯內剩餘顆數及杯外疑似掉落；不確定時改角度重拍，仍不確定則要求人工確認。")

    new_page(doc)
    add_heading(doc, "4.4 實驗設計", 2)
    add_text(doc, "研究分為標準情境、壓力情境與真實感測三部分。標準情境延續既有 1,200 組可重現場景；壓力情境提高 ASR 雜訊、遮擋、相似顏色、重複盒與相機偏移比例；真實感測使用匿名固定語句錄音及短距離 RGB-D 相機拍攝無藥性的模型藥丸。每一模擬情境由固定 seed 產生，並分成 train、calibration 與 test，測試集不得回頭調整模型或門檻。")
    add_table(
        doc,
        ["資料組", "用途", "預定規模", "禁止事項"],
        [
            ["模型訓練", "意圖與風險模型擬合", "模擬情境 50%", "不可用 test 標籤"],
            ["校正資料", "溫度縮放、Platt 或 conformal 門檻", "模擬情境 20%", "不可和 train 合併重訓"],
            ["獨立測試", "最終策略比較與失敗分析", "模擬情境 30%", "不可依結果調參"],
            ["真人語音", "CER、意圖準確率、延遲與分組結果", "經倫理確認後，預計至少 10 人 × 25 句", "不收姓名、病史或真實處方"],
            ["RGB-D 實拍", "空杯、顆數、掉落與 UNVERIFIED", "至少 120 次杯內配置", "不用模擬器座標判讀"],
        ],
        widths=[3.0, 5.3, 4.0, 4.0],
        font_size=9.5,
    )
    add_heading(doc, "4.5 比較策略與評估指標", 2)
    add_table(
        doc,
        ["策略", "決策方式"],
        [
            ["A 純視覺", "依時段顏色直接取藥，不檢查排程、重複取藥或識別碼。"],
            ["B 規則式", "通過 Layer 1 全部規則後自動執行。"],
            ["C 固定門檻", "B 加上語音與視覺原始信心均不低於 0.8。"],
            ["D 校正風險", "B 加上校正後任務風險，低風險執行、中間確認、高風險停止。"],
            ["E Conformal", "沿用 B 的 Layer 1，以校正集選取風險門檻並記錄有限樣本上界。"],
        ],
        widths=[3.5, 12.8],
        font_size=10,
    )
    add_text(doc, "主要指標為正確自動完成率、錯盒率、自動執行中的錯誤率、人工確認率及危險動作攔截率。次要指標包含意圖準確率、CER、ECE、定位誤差、夾取失敗率、平均延遲、杯內顆數完全正確率與空杯判讀正確率。每個比例以情境為單位做 2,000 次 bootstrap 取得 95% 信賴區間；同一情境下策略差異使用 exact McNemar 檢定；另以消融實驗移除語音、視覺、排程與校正特徵。")

    new_page(doc)
    add_heading(doc, "4.6 前期成果與可行性", 2)
    add_text(doc, "目前已完成 300 與 1,200 情境的 MuJoCo 實驗。以下結果僅代表既定模擬分佈，不代表真實照護成效。1,200 情境中 test 有 360 件；D 策略自動完成率 39.2%，錯盒率 0.3%，自動執行中的錯誤率 0.7%，人工確認率 10.6%，危險動作攔截率 99.5%。純視覺 A 的錯盒率為 27.5%，顯示排程與硬性規則是主要安全來源。D 與 C 各有 1 件錯誤，但 D 多正確完成 42 件，McNemar exact p < 10^-12；D 與 B 的差異不顯著。")
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run().add_picture(str(FIG_STRATEGY), width=Inches(6.35))
    add_caption(doc, "圖 3 前期策略比較，測試集 n = 360，誤差線為 bootstrap 95% 信賴區間")
    add_text(doc, "前期結果也暴露兩個重要盲點：被遮擋的重複藥盒可能無法被偵測；ASR 若把時段詞完整刪除，分類器與排程可能共同導向錯誤時段。此外，風險模型在 test 中只有 1 件錯誤，無法支持「風險模型比規則更安全」的強結論。這些限制直接形成後續壓力情境與真人語音實驗的研究問題。")

    new_page(doc)
    add_heading(doc, "4.7 研究步驟與八個月進度", 2)
    add_table(
        doc,
        ["月份", "工作內容", "可驗收產出"],
        [
            ["第 1 月", "確認研究倫理與資料管理；凍結研究問題、指標、版本與測試集。", "研究計畫、資料字典、預註冊式實驗規格"],
            ["第 2 月", "建立高遮擋、高 ASR 雜訊、重複盒與相機偏移的壓力情境。", "壓力情境產生器與單元測試"],
            ["第 3 月", "完成策略 E 正式 300/1,200 情境實驗與多 seed 重跑。", "CSV、模型、執行紀錄與失敗案例"],
            ["第 4 月", "依同意書收錄匿名固定語句；比較 Whisper small/medium。", "匿名 WAV、manifest、CER 與延遲報告"],
            ["第 5 月", "以真實短距離 RGB-D 拍攝模型藥丸；建立空杯、群聚與掉落測試。", "RGB-D 測試資料與判讀報告"],
            ["第 6 月", "整合人工覆核流程；檢查低信心、UNVERIFIED 與異常通報。", "端到端測試與操作紀錄"],
            ["第 7 月", "計算 bootstrap CI、McNemar、ECE、消融與敏感度分析。", "統計表、研究圖與限制清單"],
            ["第 8 月", "完成研究報告、程式封存、操作講義與成果展示。", "成果報告、可重現套件與簡報"],
        ],
        widths=[2.0, 8.7, 5.6],
        font_size=9.5,
    )
    add_heading(doc, "4.8 資料與研究倫理", 2)
    add_text(doc, "模擬資料以情境 seed、設定檔、決策輸出與物理執行結果保存，不包含個人資料。真人語音只收錄研究提供的 25 句固定指令，使用匿名 speaker_id；原始音檔與 manifest 分開管理，不記錄姓名、聯絡方式、病史、地址或真實用藥。受試者可隨時停止並要求刪除尚未匿名化資料。正式收錄前，將依學校規定確認是否需人體研究倫理審查或免審判定。")
    add_text(doc, "本研究不使用真實藥物、不對人提供治療建議、不讓未經安全評估的機械手臂靠近受試者。實機階段先以空杯及模型藥丸進行，採低速、限制工作空間、實體緊急停止與指導人員在場。研究成果公開時只提供匿名語音衍生指標；原始音檔是否公開須另取得明確同意。")

    # Continue on the same page so the C802 main body stays within 10 pages.
    add_heading(doc, "五 預期結果", 1)
    add_heading(doc, "5.1 預期學術成果", 2)
    add_numbered(doc, 1, "提出一套可重現的長照取藥多模態安全評估框架，清楚分離感知、規則、風險、人工覆核與物理驗證。")
    add_numbered(doc, 2, "量化校正風險、固定門檻與 conformal 策略在自動完成率、錯誤率及人工負擔間的差異，並提供信賴區間。")
    add_numbered(doc, 3, "建立語音時段詞錯誤、遮擋重複盒與 RGB-D 群聚藥丸等失敗案例分類，說明系統在哪些情況必須拒絕執行。")
    add_numbered(doc, 4, "提出以空杯判讀為主要安全目標、精確顆數為次要資訊的手腕 RGB-D 驗證流程，避免把不可靠的精確計數誤用為醫療結論。")
    add_heading(doc, "5.2 預期實作成果", 2)
    add_bullet(doc, "可執行的 CareArm AI 模擬系統、網頁儀表板、事件資料庫與初學者操作講義。")
    add_bullet(doc, "標準與壓力情境資料、策略 A 至 E 的決策紀錄、統計表與研究圖。")
    add_bullet(doc, "匿名語音評估工具、錄音腳本、同意書範本及 small/medium 模型比較。")
    add_bullet(doc, "真實 RGB-D 藥杯影像測試與模擬到真實落差分析。")
    add_heading(doc, "5.3 預期判定標準", 2)
    add_table(
        doc,
        ["項目", "目標", "解讀限制"],
        [
            ["硬性規則", "危險動作攔截率不低於 98%", "只對預先定義的測試分佈成立"],
            ["自動執行", "D/E 在錯誤率不劣於 C 時，提高正確自動完成率", "需以配對檢定及 CI 判定"],
            ["語音", "回報 CER、意圖正確率、ECE 與延遲，不只報單一 accuracy", "樣本不代表所有長者口音"],
            ["手腕 RGB-D", "空杯判讀優先達到高敏感度；不確定時輸出 UNVERIFIED", "不能證明人體吞服"],
            ["重現性", "以固定 seed、版本、CSV、測試與執行紀錄重建主要表圖", "實機仍受硬體差異影響"],
        ],
        widths=[3.1, 7.0, 6.2],
        font_size=9.5,
    )
    add_heading(doc, "5.4 可能困難與替代方案", 2)
    add_text(doc, "若風險錯誤樣本不足，將提高壓力情境比例並採多個 seed，而不以重複抽樣偽造新資訊；若真實語音收案不足，則把研究結論限定為工具可用性測試；若 RGB-D 無法分開相互接觸的藥丸，則把主要終點改為「是否為空」並以多視角補強；若實體 Panda 時間不足，完成相機離線試驗與模擬硬體在迴路測試，並明確保留實機限制。")

    # Keep this section in the 10-page main body; references start on a new page.
    add_heading(doc, "六 需要指導教授指導內容", 1)
    add_text(doc, "本計畫由學生負責程式整合、情境產生、實驗執行、資料整理與初稿撰寫；以下工作需要指導教授提供方法與安全審查。")
    add_numbered(doc, 1, "研究問題與因果界線：確認研究主張是否超過模擬與觀察資料能支持的範圍。")
    add_numbered(doc, 2, "實驗設計：審查 train/calibration/test 分離、壓力情境分佈、樣本數與多 seed 重複方式。")
    add_numbered(doc, 3, "統計方法：確認 bootstrap 單位、McNemar 配對定義、conformal 假設與多重比較的呈現方式。")
    add_numbered(doc, 4, "機械手臂安全：審查實機速度、工作空間、模型藥丸、緊急停止與操作人員站位。")
    add_numbered(doc, 5, "研究倫理與資料治理：判定真人語音收錄所需程序，確認同意書、匿名化、保存期限與公開範圍。")
    add_numbered(doc, 6, "論文寫作：逐次檢查文獻回顧、結果解讀、圖表標示及限制，避免把技術展示誤寫成臨床效益。")
    add_heading(doc, "6.1 學生預計學習成果", 2)
    add_text(doc, "學生將在八個月內學習機械手臂座標與控制、相機幾何、OpenCV、語音意圖分類、機率校正、conformal risk control、實驗資料切分、統計推論、FastAPI、SQLite、版本化測試與學術倫理。每一階段都以可執行程式、測試、紀錄及書面解釋作為學習證據，而非只以介面展示判定完成。")
    add_heading(doc, "6.2 可行性總結", 2)
    add_text(doc, "前期系統已完成 Panda 模擬夾取、俯視藥盒辨識、語音與排程決策、手腕 RGB-D 計數、異常通報、網頁儀表板及 1,200 情境實驗，證明基本技術路徑可行。後續研究集中在目前證據最薄弱的三處：策略 E 的正式大型實驗、匿名真人語音與真實 RGB-D 相機。工作內容可拆成獨立里程碑，即使實機資源延遲，仍能完成可重現的模擬、語音及相機離線研究。")
    add_text(doc, "送件前應由申請人與指導教授補齊學校、系所、年級及指導教授欄位，並依當年度校內截止日完成 C801、C803、C804、歷年成績證明及至少六小時學術倫理教育證明。", bold_lead="送件前應")

    new_page(doc)
    add_heading(doc, "七 參考文獻", 1)
    refs = [
        "[1] World Health Organization. (2017). Medication Without Harm: WHO Global Patient Safety Challenge. https://www.who.int/initiatives/medication-without-harm/",
        "[2] Maleki, M., Mardani, A., Kakemam, E., Huertas-Zurriaga, A., & Vaismoradi, M. (2025). Robot-assisted medication management in home care and long-term care settings: A mixed-method systematic review. Expert Review of Pharmacoeconomics & Outcomes Research, 25(8), 1167-1182. https://doi.org/10.1080/14737167.2025.2537190",
        "[3] Radford, A., Kim, J. W., Xu, T., Brockman, G., McLeavey, C., & Sutskever, I. (2023). Robust speech recognition via large-scale weak supervision. Proceedings of Machine Learning Research, 202, 28492-28518. https://proceedings.mlr.press/v202/radford23a.html",
        "[4] Jain, S., & Argall, B. (2020). Probabilistic human intent recognition for shared autonomy in assistive robotics. ACM Transactions on Human-Robot Interaction, 9(1), Article 2. https://doi.org/10.1145/3359614",
        "[5] Guo, C., Pleiss, G., Sun, Y., & Weinberger, K. Q. (2017). On calibration of modern neural networks. Proceedings of Machine Learning Research, 70, 1321-1330. https://proceedings.mlr.press/v70/guo17a.html",
        "[6] Angelopoulos, A. N., Bates, S., Fisch, A., Lei, L., & Schuster, T. (2024). Conformal risk control. International Conference on Learning Representations. https://proceedings.iclr.cc/paper_files/paper/2024/file/f3549ef9b5ff520a7e41ff3cc306ab2b-Paper-Conference.pdf",
        "[7] Todorov, E., Erez, T., & Tassa, Y. (2012). MuJoCo: A physics engine for model-based control. 2012 IEEE/RSJ International Conference on Intelligent Robots and Systems, 5026-5033. https://doi.org/10.1109/IROS.2012.6386109",
        "[8] Franka Emika GmbH. (2018). Panda technical data and user manual. https://panda-safe-hrc.readthedocs.io/en/latest/_downloads/deab6dbdb4210b88c2db1053111209f6/FrankaPandaManual.pdf",
        "[9] Bradski, G. (2000). The OpenCV Library. Dr. Dobb's Journal of Software Tools. https://opencv.org/",
        "[10] Pedregosa, F., et al. (2011). Scikit-learn: Machine learning in Python. Journal of Machine Learning Research, 12, 2825-2830. https://jmlr.org/papers/v12/pedregosa11a.html",
        "[11] Efron, B., & Tibshirani, R. J. (1993). An Introduction to the Bootstrap. Chapman & Hall/CRC.",
        "[12] McNemar, Q. (1947). Note on the sampling error of the difference between correlated proportions or percentages. Psychometrika, 12, 153-157. https://doi.org/10.1007/BF02295996",
        "[13] 國家科學及技術委員會。（2024）。國家科學及技術委員會補助大專學生研究計畫作業要點，113 年 11 月 1 日修正。https://law.nstc.gov.tw/LawContent.aspx?id=GL000114",
        "[14] 國家科學及技術委員會。補助大專學生研究計畫申請書表 C802 與線上申請資料。https://www.nstc.gov.tw/folksonomy/list/2af9ad9a-1f47-450d-b5a1-2cb43de8290c",
    ]
    for ref in refs:
        add_text(doc, ref, indent=False, after=6)

    doc.core_properties.title = "具不確定性感知與人工覆核機制之長照取藥機械手臂研究"
    doc.core_properties.subject = "國科會大專學生研究計畫表 C802"
    doc.core_properties.author = "程陳宥"
    doc.core_properties.keywords = "CareArm AI, 長照機器人, 多模態感知, 風險校正, Human in the loop"
    doc.save(OUT)
    print(OUT)


if __name__ == "__main__":
    build()
