"""Package verified official sources without importing or changing business data."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

BASE = Path(__file__).resolve().parent
SOURCES = BASE / 'sources'
CURATED = BASE / 'curated'
OUTPUT = Path(r'C:\Users\XOS\Documents\Codex\2026-09-30\c-users-xos-desktop-sol\outputs\research')
CURATED.mkdir(exist_ok=True)
OUTPUT.mkdir(parents=True, exist_ok=True)
CHECKED_ON = '2026-10-01'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def slice_text(value: str, start: str | None, end: str | None) -> str:
    if start is not None:
        value = value[value.index(start):]
    if end is not None:
        value = value[:value.index(end)]
    return value.strip()


def record(ident, archive, title, publisher, category, usage, publication, number=None,
           signed=None, school=None, campus=None, scope=None, expiry=None,
           version=None, effective=None, validity=None, notes=None,
           quotes=(), start=None, end=None):
    metadata = json.loads((SOURCES / f'{archive}.json').read_text(encoding='utf-8'))
    raw_path = Path(metadata['raw_path'])
    text_path = SOURCES / f'{archive}.txt'
    value = text_path.read_text(encoding='utf-8')
    for quote in quotes:
        assert quote in value, (ident, 'Quote not found verbatim', quote)
    assert digest(raw_path) == metadata['sha256'], (ident, 'Raw content checksum changed')
    body = slice_text(value, start, end)
    entry = {
        'id': ident,
        'title': title,
        'publisher': publisher,
        'document_number': number,
        'source_url': metadata['requested_url'],
        'verified_final_url': metadata['final_url'],
        'verified_on': CHECKED_ON,
        'verification_status': 'official_body_verified',
        'verification_method': 'official_public_page_body_or_linked_attachment',
        'capture_method': metadata.get('capture_method', 'public_HTTP_fetch_with_TLS_verification_when_HTTPS'),
        'retrieved_at_utc': metadata.get('fetched_at_utc'),
        'publication_date': publication,
        'signed_date': signed,
        'effective_from': effective,
        'expires_at': expiry,
        'version': version,
        'category': category,
        'rag_usage': usage,
        'default_current_answer_allowed': usage in ('school_fact', 'general_policy', 'official_explanation', 'labor_reference'),
        'school': school,
        'campus': campus,
        'jurisdiction_and_applicability': scope,
        'validity_evidence': validity or '网页未标注效力状态；已核验正文，未作穷尽式法律有效性审查。',
        'limitations': notes or [],
        'verbatim_evidence': list(quotes),
        'raw_path': str(raw_path),
        'raw_relative_path': f'sources/{raw_path.name}',
        'raw_sha256': digest(raw_path),
        'capture_metadata_relative_path': f'sources/{archive}.json',
        'archived_page_text_relative_path': f'sources/{text_path.name}',
        'archived_page_text_sha256': digest(text_path),
        'curated_text_relative_path': f'curated/{ident}.txt',
    }
    header = '\n'.join([
        f'【资料编号】{ident}', f'【标题】{title}', f'【发布机构】{publisher}',
        f'【官方原文】{entry["source_url"]}',
        f'【网页发布日期】{publication or "未标注"}', f'【核验日期】{CHECKED_ON}',
        f'【用途】{usage}', f'【适用范围】{scope}',
        '【边界】' + '；'.join(entry['limitations']),
        '【处理说明】以下为官方正文节选或主体正文，保留原文；页首检索元数据由核验者整理。',
        '', '【原文】', body,
    ])
    curated_path = CURATED / f'{ident}.txt'
    curated_path.write_text(header + '\n', encoding='utf-8')
    entry['curated_text_sha256'] = digest(curated_path)
    return entry


wage_path = SOURCES / 'hubei-minimum-wage-2025.browser.txt'
wage_url = 'https://www.hubei.gov.cn/zfwj/ezbf/202512/t20251226_5842830.shtml'
wage_meta = {
    'requested_url': wage_url,
    'final_url': wage_url,
    'id': 'hubei-minimum-wage-2025.browser',
    'raw_path': str(wage_path),
    'sha256': digest(wage_path),
    'content_type': 'text/plain; charset=utf-8',
    'capture_method': 'CUA in-app browser visible body text; direct public HTTP request returned 412',
    'fetched_at_utc': datetime.fromtimestamp(wage_path.stat().st_mtime, timezone.utc).isoformat(),
    'timestamp_basis': 'local archive modification timestamp',
}
(SOURCES / 'hubei-minimum-wage-2025.browser.json').write_text(
    json.dumps(wage_meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

entries = [
    record('S01', '1926dc129f7e', '武汉设计工程学院2026年全日制普通本、专科招生章程',
           '武汉设计工程学院招生办公室', 'school_charter', 'school_fact', '2026-05-22',
           school='武汉设计工程学院', campus='武汉校区、红安教学改革基地', version='2026年',
           scope='本校2026年招生章程；第十六条用于确认勤工助学制度存在，第二十一条用于校区身份。',
           notes=['没有规定学校具体时薪、考勤结算或审批细则。'],
           quotes=['学校建立勤工助学制度，为学有余力的学生提供校内外勤工助学岗位，学生按劳取酬。',
                   '武汉校区：湖北省武汉市江夏区藏龙岛杨桥湖大道1号'],
           start='第一章 总则', end='\n返回'),
    record('S02', '2e8540bb90be', '党委学生工作部部门概况',
           '武汉设计工程学院党委学生工作部', 'school_department', 'school_fact', None,
           school='武汉设计工程学院', scope='本校公开部门职责和咨询渠道。',
           notes=['动态页面未标注发布日期；电话号码以核验时公开页面为依据。',
                  '该页称经济资助中心，其他通知称学生资助管理中心，不推定内部机构变更。'],
           quotes=['学生工作处设办公室、教育管理科、经济资助中心、心理健康教育中心。工作职责：',
                   '6.负责全校学生奖、助、贷、勤、补、减、免等经济资助工作;',
                   '经济资助中心027-81733022'],
           start='学生工作处是在学校党委', end='\n武汉设计工程学院版权所有'),
    record('S03', '26082ae49029', '关于学生工作部（处）招聘学生助理的通知',
           '武汉设计工程学院学生工作部（处）', 'historical_recruitment', 'archive_only',
           '2025-10-28', signed='2025-10-28', school='武汉设计工程学院', campus='武汉校区',
           scope='2025年学生工作部（处）学生助理招聘这一具体批次。', expiry='2025-10-31T14:00:00+08:00',
           notes=['报名已截止；不能作为当前在招岗位。', '4-8小时值班和资格审查/面试仅为该次招聘要求。',
                  '未公布酬金数额；该通知不能证明全校统一申请流程。'],
           quotes=['在校学生（武汉校区），能保证相对稳定的工作时间。',
                   '（四）时间充裕，每周至少能保证4-8小时的值班时间（可根据个人课表进行协调）。',
                   '（五）同等条件下，已通过家庭经济困难认定的学生优先录用。'],
           start='为充分发挥勤工助学实践育人功能', end='\n关于2024-2025学年评选'),
    record('S04', '1caa71151726', '学生工作部（处）学生助理申请表（2025-2026学年）',
           '武汉设计工程学院学生工作部（处）', 'historical_blank_form', 'archive_only',
           '2025-10-28', school='武汉设计工程学院', campus='武汉校区', version='2025-2026学年',
           scope='S03官方正文所链接的空白DOCX附件；用于历史表单字段参考。',
           expiry='2025-10-31T14:00:00+08:00',
           notes=['发布日期取所属通知，不是附件独立发布日期。', '这是空白表，不包含已填学生资料。',
                  '并非已确认适用于2026年新招聘的报名表。']),
    record('S05', '90d0d2ec1917', '武汉设计工程学院 藏龙美术馆 勤工助学岗位招聘启事',
           '武汉设计工程学院藏龙美术馆', 'historical_recruitment', 'archive_only',
           '2025-03-18', school='武汉设计工程学院', scope='2025年藏龙美术馆具体招聘批次。',
           expiry='2025-04-30', notes=['报名已截止；不能作为当前在招岗位。',
                  '正文未写具体酬金金额。', '正文提到报名表附件，本次未发现可核验下载链接，不提供猜测文件。'],
           quotes=['1. 工作时间灵活，可根据课程安排协调排班；',
                   '2. 薪酬按照学校勤工助学标准执行。', '报名截止日期： 2025年4月30日'],
           start='为进一步丰富校园文化', end='\n2026 届湖北美术类'),
    record('S06', '0ccd37658334', '关于开展2023年家庭经济困难学生认定工作的通知',
           '武汉设计工程学院党委学生工作部', 'historical_annual_notice', 'archive_only',
           '2023-10-10', school='武汉设计工程学院', version='2023年认定批次',
           scope='2023年本校全日制普通教育本专科在校学生困难认定。',
           notes=['通知引用武设院[2020]21号，但本次未取得该2020实施办法全文。',
                  '2023年10月22日上报时间不能用于2026年。', '可证明存在相应认定依据，不据此自动认定资格。'],
           quotes=['根据《武汉设计工程学院家庭经济困难学生认定工作实施办法》（武设院[2020]21号）精神，学校决定开展2023年家庭经济困难学生认定工作。',
                   '全日制普通教育本专科在校学生。', '工作中要注意方式方法，不能让学生当众诉苦、互相比困。'],
           start='各学院：', end='\n【学生资助】关于开展2023年国家'),
    record('S07', '7a1f0b86f6f8', '武汉设计工程学院家庭经济困难学生认定工作实施办法（2019年公开页面）',
           '武汉设计工程学院信息公开网', 'historical_school_policy', 'archive_only',
           '2019-05-30', school='武汉设计工程学院', version='2019年公开页面版本',
           scope='历史全文；现行效力未确认。',
           validity='网页未标注效力状态；2023通知已引用2020年版实施办法，该旧页面不能直接当现行规则。',
           notes=['不得把两档困难等级、公示天数、民政盖章要求直接写入当前本校流程。',
                  '其民政证明环节还须与S11取消证明通知核对；未认定该旧页面本身的正式废止状态。'],
           quotes=['第四条 家庭经济困难学生的认定等级设“家庭经济困难”和“家庭经济特别困难”两个等级。'],
           start='第一章 总则', end='\n返回列表'),
    record('S08', 'bfb264520316', '教育部 财政部关于印发《高等学校勤工助学管理办法（2018年修订）》的通知及附件',
           '教育部、财政部', 'national_general_policy', 'general_policy', '2018-09-03',
           number='教财〔2018〕12号', signed='2018-08-20', version='2018年修订',
           scope='全日制普通本科/高职/高专的本专科生和研究生；学校组织的勤工助学。学生自行校外兼职不属此办法范围。',
           notes=['这是国家通用规则，并非武汉设计工程学院实施细则。',
                  '每周8小时、每月40小时为原则要求，寒暑假可按校情适当延长。',
                  '12元/小时为校内临时岗原则标准，不是该校已确认时薪。',
                  '正文自公布日起施行；S09官方解释明确2018-08-20施行，网页生成日期为2018-08-24。'],
           quotes=['第六条 勤工助学活动由学校统一组织和管理。学生私自在校外兼职的行为，不在本办法规定之列。',
                   '第十二条 根据本办法规定，结合学校实际情况，制定完善本校学生勤工助学活动的实施办法。',
                   '学生参加勤工助学的时间原则上每周不超过8小时，每月不超过40小时。寒暑假勤工助学时间可根据学校的具体情况适当延长。',
                   '第二十五条 校内固定岗位按月计酬。以每月40个工时的酬金原则上不低于当地政府或有关部门制定的最低工资标准或居民最低生活保障标准为计酬基准，可适当上下浮动。',
                   '第二十六条 校内临时岗位按小时计酬。每小时酬金可参照学校当地政府或有关部门规定的最低小时工资标准合理确定，原则上不低于每小时12元人民币。'],
           start='高等学校学生勤工助学管理办法\n（2018年修订）', end='\n点击右上角'),
    record('S09', 'b675e37d210e', '教育部财务司、全国学生资助管理中心负责人就勤工助学管理办法答记者问',
           '教育部财务司、全国学生资助管理中心', 'national_official_explanation', 'official_explanation',
           '2018-09-12', version='解释2018年办法',
           scope='解释S08的背景、岗位设置、计酬及学校制定实施办法等。',
           notes=['官方解释资料；具体规则优先核对S08正文。'],
           quotes=['临时岗位按小时计酬，每小时酬金可参照学校当地政府或有关部门规定的最低小时工资标准合理确定，原则上不低于每小时12元人民币。'],
           start='近日，教育部、财政部联合印发了', end='\n（责任编辑：'),
    record('S10', 'e1000a9604c7', '教育部等六部门关于做好家庭经济困难学生认定工作的指导意见',
           '教育部、财政部、民政部、人力资源社会保障部、国务院扶贫办、中国残联（文件原发布机构）',
           'national_general_policy', 'general_policy', '2018-11-06', number='教财〔2018〕16号',
           signed='2018-10-30', version='2018年', scope='文件所列各级各类学校学生，含全日制普通高校本专科生和全日制研究生。',
           notes=['不能据此推定武汉设计工程学院当前具体认定档次或数值阈值。',
                  '机构名称与特殊群体用语保留2018年原文，不代表2026年机构称谓。'],
           quotes=['严禁让学生当众诉苦、互相比困。',
                   '家庭经济困难学生认定工作原则上每学年进行一次，每学期要按照家庭经济困难学生实际情况进行动态调整。',
                   '工作程序一般应包括提前告知、个人申请、学校认定、结果公示、建档备案等环节。各地、各校可根据实际情况制定具体的实施程序。',
                   '公示时，严禁涉及学生个人敏感信息及隐私。'],
           start='各省、自治区、直辖市教育厅', end='\n点击右上角'),
    record('S11', '4005468d7453', '教育部关于取消一批证明事项的通知',
           '教育部', 'national_general_policy', 'general_policy', '2019-04-23',
           number='教政法函〔2019〕12号', signed='2019-03-29', version='2019年',
           scope='仅选取与高校学生资助家庭经济情况证明相关的第二部分第一项。',
           notes=['网页生成日期2019-04-02，正文落款2019-03-29，发布日期2019-04-23；分别记录，不混用。',
                  '不能把取消民政证明解释为取消全部支撑材料或学校认定。'],
           quotes=['高校学生申请资助时需由家庭所在地乡、镇或街道民政部门对学生家庭经济情况予以证明的环节，改为申请人书面承诺。'],
           start='二、取消部门规范性文件设定的12项证明事项', end='\n（二）取消《教育部 中国残联'),
    record('S12', '13d99a7aa186', '最低工资规定（湖北省人社厅公开转载）',
           '劳动和社会保障部；湖北省人力资源和社会保障厅转载', 'national_labor_rule',
           'labor_reference', '2006-12-01', number='劳动和社会保障部令第21号',
           signed='2004-01-20', effective='2004-03-01',
           scope='用人单位和与其形成劳动关系的劳动者；月、小时最低工资形式和适用条件。',
           validity='湖北省人社厅网页效力状态标注“有效”；核验于2026-10-01。',
           notes=['2006-12-01是转载网页日期；规章公布日2004-01-20，施行日2004-03-01。',
                  '不得把劳动关系最低工资与本校全部勤工助学实际报酬直接等同。'],
           quotes=['月最低工资标准适用于全日制就业劳动者，小时最低工资标准适用于非全日制就业劳动者。'],
           start='劳动和社会保障部令第21号', end='\n附件:'),
    record('S13', 'hubei-minimum-wage-2025.browser', '湖北省人民政府办公厅关于调整全省最低工资标准的通知',
           '湖北省人民政府办公厅', 'provincial_labor_standard', 'labor_reference', '2025-12-26',
           number='鄂政办发〔2025〕44号', signed='2025-12-24', effective='2025-12-01', version='2025年调整',
           scope='湖北省用人单位和与其建立劳动关系的劳动者；附件第一档适用区域原文为“武汉市区”等。',
           validity='湖北省政府网页效力状态标注“有效”；核验于2026-10-01。',
           notes=['浏览器打开并核验正文；原HTTP工具返回412，不使用错误页作证据。',
                  '2400元/月、24元/小时是附件第一档劳动最低工资，不能声称本校时薪为24元。',
                  '附件使用“武汉市区”而未逐项列出江夏区；本报告按原文引用，不扩写附件适用区划。',
                  '本次查到的新标准执行日2025-12-01，发文和发布日期更晚，按正文如实记录。'],
           quotes=['一、全日制就业劳动者月最低工资标准按区域划分为三档，依次为2400元、2130元、1970元。各档标准及适用区域见附件。',
                   '二、非全日制就业劳动者小时最低工资标准依次为24元、21.5元、20元。各档标准及适用区域见附件。',
                   '七、调整后的最低工资标准自2025年12月1日起执行。'],
           start='各市、州、县人民政府，省政府各部门：', end='\n编辑：'),
    record('S14', '4a1edf34318f', '教育部办公厅关于切实做好2025年秋季学期高校学生资助工作的通知',
           '教育部办公厅', 'historical_annual_notice', 'archive_only', '2025-09-19',
           number='教财厅函〔2025〕14号', signed='2025-09-17', version='2025年秋季学期',
           scope='2025年秋季高校学生资助部署；困难救助、认定服务和政策告知的背景资料。',
           notes=['年度部署通知不能当作2026年批次申请公告。',
                  '以正文和发布日期为准，不把URL路径中20251118当发布日期。'],
           quotes=['设立并畅通便捷高效的资助申请申诉渠道，及时回应处理学生诉求。'],
           start='各省、自治区、直辖市教育厅', end='\n点击右上角'),
]

image_path = SOURCES / '996ac9bce68f.png'
assert digest(image_path) == 'f36f594645517031dc8fa72c176d99dea64d87a666e9d98f926136f91228b4f9'
table_text = '第一档：月最低工资标准2400元；非全日制小时最低工资标准24元。适用区域：武汉市区；襄阳市襄城区、樊城区；宜昌市西陵区、伍家岗区、点军区、猇亭区。'
transcription_path = CURATED / 'S13_attachment_first_tier.txt'
transcription_path.write_text('【说明】人工查看官方附件图片后转录第一档；请结合原图核对。\n' + table_text + '\n', encoding='utf-8')
entries[12]['attachments'] = [{
    'title': '湖北省分区域最低工资标准（官方附件图片）',
    'source_url': 'https://www.hubei.gov.cn/zfwj/ezbf/202512/W020251226541820870223.png',
    'raw_relative_path': 'sources/996ac9bce68f.png',
    'raw_sha256': digest(image_path),
    'verification_status': 'official_attachment_visually_verified',
    'transcription_scope': '仅第一档人工转录，其余两档未编入可回答区域映射。',
    'transcription_relative_path': 'curated/S13_attachment_first_tier.txt',
    'transcription_sha256': digest(transcription_path),
    'first_tier_transcription': table_text,
}]
catalog = {
    'title': '武汉设计工程学院勤工助学服务项目：官方资料核验清单',
    'checked_on': CHECKED_ON,
    'timezone': 'Asia/Shanghai',
    'school': '武汉设计工程学院',
    'primary_campus': '武汉市江夏区藏龙岛杨桥湖大道1号（依据S01）',
    'method': '打开官方正文及实际附件后记录；搜索结果仅用于发现链接，不作为正文证据。原始缓存、归一化文本、SHA-256保留。',
    'scope_of_work': '公开资料只读核验；没有导入业务数据库、创建真实岗位或联系学校。',
    'not_found_or_unconfirmed': [
        '本校完整且已确认现行的勤工助学管理/实施办法全文。',
        '本校具体岗位数值时薪、固定岗月酬、结算周期与考勤审批细则。',
        '武设院[2020]21号实施办法全文与本校2026年困难认定完整细则。',
        '2026年当前仍可报名的勤工助学招聘批次。',
    ],
    'global_limitations': [
        '未发现公开全文不等于学校没有内部政策。',
        '真实性、日期、适用范围、是否截止分别记录；官方来源不自动代表适用于当前业务。',
        '没有把历史困难等级或国家/劳动最低工资数额推定为本校现行规定。',
        '未收集学生困难认定受助名单；历史招聘表单是空白附件。',
    ],
    'sources': entries,
}
catalog_path = BASE / 'verified_sources.json'
catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

report = r'''# 武汉设计工程学院勤工助学项目：官方资料核验报告

核验日期：2026年10月1日（北京时间）。研究范围：武汉设计工程学院武汉校区，以及可用于项目说明的国家、湖北省官方材料。只采用已经打开核验的官方正文或实际附件；搜索摘要不作为政策证据。

## 已确认的事实

**学校确实建立了勤工助学制度。** [学校2026年招生章程](https://zb.wids.edu.cn/view/32234.html)第十六条明确：“学校建立勤工助学制度，为学有余力的学生提供校内外勤工助学岗位，学生按劳取酬。”第二十一条给出武汉校区地址：湖北省武汉市江夏区藏龙岛杨桥湖大道1号。学校标识码4142014035，可用于区分同名或相似名称的学校。[S01]

**查到了两份真实的校内招聘，但均为历史批次。** 2025年学生工作部学生助理招聘限武汉校区，2025年10月31日14:00截止；藏龙美术馆招聘2025年4月30日截止。二者可作为需求分析、历史岗位样例，不能在系统中标为“当前在招”。[S03、S05]

**学校具体时薪尚无可核验的公开数值。** 美术馆招聘仅写“薪酬按照学校勤工助学标准执行”；学生助理通知也没有数额。本次未找到完整且已确认现行的学校勤工助学实施办法、具体时薪、固定岗位月酬和考勤结算细则。未发现公开全文不等于学校没有政策，不得补写一个“学校规定”。

## 学校资料

| 编号与官方材料 | 网页日期 | 已核验内容 | 项目用途及边界 |
| --- | --- | --- | --- |
| S01：[2026年全日制普通本、专科招生章程](https://zb.wids.edu.cn/view/32234.html) | 2026-05-22 | 第16条勤工助学制度；第21条校区地址 | 可回答“学校是否有勤工助学制度”；没有具体酬金和审批细则 |
| S02：[学生工作部部门概况](https://xgb.wids.edu.cn/list/133.html) | 未标注 | 学生工作处设经济资助中心，负责“奖、助、贷、勤、补、减、免”；公开电话027-81733022 | 可提供核验时公开的咨询渠道；不能推定内部审批人员 |
| S03：[招聘学生助理通知](https://xgb.wids.edu.cn/view/28689.html) | 2025-10-28 | 武汉校区在校生；Office能力；每周4-8小时值班；同等条件下已通过困难认定者优先；资格审查后面试 | 已截止，历史案例。工作时间和选拔流程仅属于该次招聘 |
| S04：[学生助理申请表DOCX](https://xgb.wids.edu.cn/upload/20251028/69006533dd38a.docx) | 依据所属通知2025-10-28 | 已下载打开附件，确认是2025-2026学年空白表 | 仅用于历史表单字段参考，不能冒充当前报名表 |
| S05：[藏龙美术馆勤工助学招聘](https://msg.wids.edu.cn/view/26272.html) | 2025-03-18 | 活动、布展、导览、宣传与空间管理；排班配合课程；酬金依学校标准 | 2025-04-30截止，历史案例。正文提到的报名表未发现可核验下载链接 |
| S06：[2023年家庭经济困难学生认定通知](https://xgb.wids.edu.cn/view/19421.html) | 2023-10-10 | 明确引用《武汉设计工程学院家庭经济困难学生认定工作实施办法》（武设院[2020]21号）；本专科在校生；认定工作要求 | 年度历史通知。2023年上报期限不能用于2026年；所引2020年办法全文本次未取得 |
| S07：[2019年公开的困难认定实施办法](https://xxgk.wids.edu.cn/view/12571.html) | 2019-05-30 | 完整16条及空白表；旧页面写两档困难等级、民政盖章等 | **现行效力未确认，仅存档**。2023年已引用2020年办法；不得直接采用旧版等级、盖章或公示天数 |

S03原文：“（五）同等条件下，已通过家庭经济困难认定的学生优先录用。”这不能改写成“只有困难学生才允许申请所有勤工助学岗位”。S05原文：“2. 薪酬按照学校勤工助学标准执行。”这不能改写成任何具体数额。

## 国家及湖北省官方资料

| 编号与官方材料 | 文号和日期 | 已核验内容 | 适用边界 |
| --- | --- | --- | --- |
| S08：[教育部、财政部勤工助学管理办法（2018年修订）](https://www.moe.gov.cn/srcsite/A05/s7505/201809/t20180903_347076.html) | 教财〔2018〕12号；正文落款2018-08-20；网页发布2018-09-03 | 定义、组织职责、工时、固定/临时岗位、酬金和校外三方协议 | 国家通用规则；学校仍须制定实施办法；学生自行在校外兼职不在其范围 |
| S09：[教育部财务司、全国学生资助管理中心答记者问](https://www.moe.gov.cn/jyb_xwfb/s271/201809/t20180912_348406.html) | 网页2018-09-12 | 官方解释2018年办法，明确其2018-08-20施行 | 可用于概念解释，具体规定优先引用S08 |
| S10：[六部门家庭经济困难学生认定指导意见](https://www.moe.gov.cn/srcsite/A05/s7505/201811/t20181106_353764.html) | 教财〔2018〕16号；落款2018-10-30；网页2018-11-06 | 自愿申请、定量与定性结合、认定程序、复核与动态调整、保护隐私 | 不提供武汉设计工程学院当前困难等级或数值阈值 |
| S11：[教育部取消一批证明事项通知](https://www.moe.gov.cn/srcsite/A02/s7049/201904/t20190423_379235.html) | 教政法函〔2019〕12号；落款2019-03-29；生成日期2019-04-02；网页2019-04-23 | 高校申请资助的乡镇/街道民政家庭经济情况证明环节改为书面承诺 | 取消该证明环节不等于取消全部材料或学校审核 |
| S12：[最低工资规定（湖北省人社厅转载）](https://rst.hubei.gov.cn/zfxxgk/zc/qtzdgkwj/200612/t20061201_704560.shtml) | 劳动和社会保障部令第21号；公布2004-01-20；施行2004-03-01；转载2006-12-01 | 劳动关系适用范围、月/小时标准、工资构成；网页标注“有效” | 劳动用工规则，不能直接当作本校勤工助学实际时薪 |
| S13：[湖北省最低工资调整通知](https://www.hubei.gov.cn/zfwj/ezbf/202512/t20251226_5842830.shtml)及[官方附件表](https://www.hubei.gov.cn/zfwj/ezbf/202512/W020251226541820870223.png) | 鄂政办发〔2025〕44号；发文2025-12-24；发布2025-12-26；**2025-12-01起执行** | 月标准2400/2130/1970元；小时24/21.5/20元；页面标注“有效”；正文和附件均已核验 | 附件第一档写“武汉市区”；适用于劳动关系。24元不是已确认的学校勤工助学时薪 |
| S14：[2025年秋季高校资助工作通知](https://www.moe.gov.cn/srcsite/A05/s7505/202511/t20251118_1420660.html) | 教财厅函〔2025〕14号；落款2025-09-17；网页发布2025-09-19 | 困难救助、规范认定、申诉渠道和政策宣传 | 2025年秋季部署，背景资料；不能当作2026年申请通知 |

教育部部分链接实际跳转到其HTTP官方页面，缓存记录了最终地址。日期均按正文落款、网页发布日期分别记录，不从网址路径或搜索摘要推算。S08、S10、S11等页面未提供“现行有效”标记；本次确认的是官方正文真实性和具体条款，没有完成穷尽式法律有效性审查。

S08第二十一条原文：“学生参加勤工助学的时间原则上每周不超过8小时，每月不超过40小时。寒暑假勤工助学时间可根据学校的具体情况适当延长。”因此工时规则需保留“原则”和假期例外。

S08第二十五条规定校内固定岗位按月计酬；第二十六条规定校内临时岗位按小时计酬，原则上不低于每小时12元；第二十七条规定校外酬金由用人单位、学校与学生协商并写入协议。系统不能把所有岗位统一硬编码为“12元/小时”。

S13官方附件第一档转录：月最低工资标准2400元，非全日制小时最低工资标准24元；适用区域为“武汉市区；襄阳市襄城区、樊城区；宜昌市西陵区、伍家岗区、点军区、猇亭区”。附件没有逐项列出江夏区，本报告按原文引用区划，不扩写为学校内部薪酬规定。

## 建议如何用于RAG及业务功能

1. 默认政策问答可使用学校制度事实S01、公开部门职责S02，以及国家通用规则S08、S10、S11。S09作为解释；S12、S13仅作为劳动标准参考，回答时说明适用范围。
2. S03-S07和S14放入历史资料库。用户明确询问对应年份时才引用，答案标明日期和已截止/效力未确认状态；不要据此创建当前真实岗位。
3. 每份文档保存来源URL、发布机构、文号、网页日期、落款日期、版本、执行日、截止日、学校/校区、适用范围、核验状态、缓存路径和内容哈希。检索时先筛选范围与时效，再生成带来源的答案。
4. “本校具体时薪”“当前在哪里报名”“2026年困难认定有几档”应说明本次公开资料未确认，并提供学校公开咨询渠道。演示岗位、演示时薪、系统自设审批流程应标成演示数据/项目设计，不署名为学校政策。
5. 岗位申请、工时、工资明细来自业务数据库；政策文档用于解释依据。不得由模型自行补出资格结论、审批结果或学校收费/薪酬标准。

可用于验收的问答：

| 问题 | 合格回答边界 |
| --- | --- |
| 武汉设计工程学院有没有勤工助学制度？ | 引用S01第16条确认存在 |
| 我应该找哪个部门咨询？ | 引用S02公开的经济资助中心及电话，标明核验日期 |
| 学校勤工助学一小时多少钱？ | 说明具体数值未确认；不能答“12元”或“24元就是本校时薪” |
| 勤工助学每周能工作多久？ | 引用S08国家原则8小时/周、40小时/月，并保留假期例外与学校细则边界 |
| 2025年学生助理要求是什么，现在能报名吗？ | 引用S03该次要求，并明确报名已于2025-10-31截止 |
| 本校当前困难认定是不是两档、需要民政盖章？ | 不直接采用S07旧版；说明现行学校全文未确认，核对S11取消证明环节 |

## 核验记录与文件

资料包包含14条核验目录记录及S13官方图片附件。每条保存原始缓存和SHA-256，并生成去除网站菜单后的主体正文或节选。正文节选保留原文；资料编号、范围和使用限制属于核验者整理的元数据。没有把资料导入业务数据库，也没有联系学校。

- `verified_sources.json`：可机器读取的资料目录，含原文引句、日期、适用范围及哈希。
- `sources/`：官方HTML、浏览器正文、DOCX、图片及原始抓取元数据。
- `curated/`：按S01-S14组织的清洁文本，以及S13附件第一档转录。

本次检查了学校主站、学生工作部、招生信息网、美术馆、信息公开网，以及教育部、湖北省政府、人社厅公开页面。校内全文检索未找到完整现行勤工助学实施办法；2020年困难认定办法只核验到2023年通知中的引用。省学生资助平台访问时显示系统维护，因此没有用其维护页或不可访问材料作政策依据。
'''

report_path = BASE / 'VERIFIED_SOURCES_2026-10-01.md'
report_path.write_text(report + '\n', encoding='utf-8')
shutil.copy2(report_path, OUTPUT / '武汉设计工程学院_官方资料核验报告.md')
shutil.copy2(catalog_path, OUTPUT / 'verified_sources.json')
for entry in entries:
    for key in ('raw_relative_path', 'archived_page_text_relative_path', 'curated_text_relative_path', 'capture_metadata_relative_path'):
        source_file = BASE / entry[key]
        target_file = OUTPUT / entry[key]
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)
    for attachment in entry.get('attachments', []):
        for key in ('raw_relative_path', 'transcription_relative_path'):
            source_file = BASE / attachment[key]
            target_file = OUTPUT / attachment[key]
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target_file)

print(json.dumps({
    'records_verified': len(entries),
    'report': str(report_path),
    'catalog': str(catalog_path),
    'output_report': str(OUTPUT / '武汉设计工程学院_官方资料核验报告.md'),
    'checks': 'All quoted evidence found verbatim; all original-file SHA-256 values verified; copied selected sources and clean texts.',
}, ensure_ascii=False, indent=2))
