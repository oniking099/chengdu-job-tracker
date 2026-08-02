#!/usr/bin/env python3
"""
成都气象环境AI招聘信息日报 v6
改进：
1. 5轮搜索(2:00/3:00/4:00/5:00/6:00) + 9:00合并推送
2. 搜索范围扩大：不限定国企央企，新增"其他"类别(薪资≥1.8万)
3. 研究所类别含学校（大学/学院归为研究所）
4. 其他类别排在最下方
5. 修复查看详情链接：用idx匹配原始数据，强制使用抓取到的真实URL
6. 排除互联网大厂、年龄35岁以下、仅限党员、博士学历要求
7. 排除互联网金融/游戏/智能驾驶/证券期货/广告传媒/法律财务等行业
"""

import os
import sys
import json
import time
import random
import re
import requests
import urllib.parse
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

# ============ 配置 ============
ZHIPU_API_KEY = os.environ.get('ZHIPU_API_KEY', '')
SERVERCHAN_KEY = os.environ.get('SERVERCHAN_KEY', '')
REPORT_DIR = os.environ.get('REPORT_DIR', 'reports')
RAW_DIR = os.path.join(REPORT_DIR, 'raw')
ZHIPU_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

MIN_SALARY = 12000  # 最低月薪12000元

# 公司类型排序优先级：国企→央企→外资→合资→研究所(含学校)→待确认→其他
TYPE_PRIORITY = {
    '国企': 0,
    '央企': 1,
    '外资': 2, '外企': 2,
    '合资': 3, '中外合资': 3, '合资企业': 3,
    '研究所': 4, '事业单位': 4, '学校': 4, '大学': 4, '院校': 4,
    '待确认': 5, '未知': 5,
    '其他': 6, '民营': 6, '私营': 6, '股份制': 6, '民企': 6,
}

# 其他类别最低薪资门槛（高于普通类别的1.2万）
OTHER_MIN_SALARY = 18000


def get_type_priority(type_str):
    """获取公司类型排序优先级"""
    if not type_str:
        return 7
    for key, priority in TYPE_PRIORITY.items():
        if key in type_str:
            return priority
    return 7


# 5轮搜索关键词分配（避免重复搜索）
KEYWORD_BATCHES = {
    1: ["气象", "大气科学", "气候", "气象算法"],
    2: ["环境科学", "环境工程", "生态环境", "环保"],
    3: ["大模型", "LLM", "AI agent", "人工智能算法"],
    4: ["深度学习", "机器学习", "数据科学", "AI应用"],
    5: ["气候模型", "环境监测", "AI研发", "算法工程师"],
}

# Boss/58 使用精简关键词
LITE_KEYWORDS = {"气象", "环境科学", "大模型", "AI", "气候", "机器学习", "算法工程师"}

# 城市代码
JOB51_CITY = "090200"      # 51job 成都
LIEPIN_CITY = "280"         # 猎聘 成都
ZHAOPIN_CITY = "801"        # 智联招聘 成都
BOSS_CITY = "101270100"     # Boss直聘 成都

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# 排除关键词（兼职/校招/实习）
EXCLUDE_KEYWORDS = ["校招", "校园招聘", "应届", "实习", "兼职", "寒暑假", "暑期", "寒假", "暑假"]

# 博士学历排除（要求博士的不要）
PHD_EXCLUDE_KEYWORDS = ["博士", "phd", "PhD", "博士研究生", "博士学历", "博士以上", "博士及以上"]

# 年龄限制排除关键词（要求35岁以下的排除）
AGE_EXCLUDE_KEYWORDS = ["35岁以下", "35周岁以下", "30岁以下", "30周岁以下", "28岁以下", "25岁以下"]

# 党员限制排除关键词（只招党员的排除）
PARTY_EXCLUDE_KEYWORDS = ["仅限党员", "须为党员", "必须是党员", "要求党员", "党员优先", "限党员"]

# 行业过滤库（排除这些行业的公司/职位）
# 互联网金融、游戏、智能驾驶、证券/期货、广告传媒、法律财务等
EXCLUDED_INDUSTRIES = [
    # 互联网金融
    "互联网金融", "网络借贷", "p2p", "P2P", "消费金融", "小额贷款", "小贷",
    "金融科技", "fintech", "FinTech", "互联网保险", "互联网理财",
    # 游戏
    "游戏", "网游", "手游", "游戏开发", "游戏设计", "游戏引擎", "unity", "Unity",
    "unreal", "Unreal", "游戏发行", "电竞", "game",
    # 智能驾驶/自动驾驶
    "智能驾驶", "自动驾驶", "无人驾驶", "智能网联", "车联网", "车载",
    "autonomous", "autonomous driving", "ADAS", "自动驾驶系统",
    # 证券/期货
    "证券", "期货", "基金管理", "券商", "投行", "投资银行", "资产管理",
    "股票", "外汇", "黄金", "大宗商品", "量化交易", "量化投资",
    # 广告传媒
    "广告", "传媒", "营销", "公关", "品牌推广", "媒介", "新媒体",
    "短视频", "直播", "内容运营", "广告投放", "程序化广告",
    # 法律财务
    "律师事务所", "律师", "法务", "legal", "Legal",
    "会计师事务所", "审计", "税务", "财税", "记账", "代理记账",
    "财务外包", "财务咨询",
    # 其他不相关行业
    "房地产", "物业", "中介", "保险公司", "担保", "典当", "拍卖",
    "教育培训", "教培", "在线教育", "k12", "K12",
    "物流", "快递", "配送", "仓储",
    "餐饮", "酒店", "旅行社", "旅游业", "民宿",
    "服装", "鞋帽", "零售", "百货", "商超", "便利店",
    "建筑", "施工", "装修", "建材",
    "农业", "种植", "养殖", "畜牧",
]

# 互联网大厂黑名单（从搜索结果中排除这些公司）
INTERNET_GIANTS = [
    "华为", "阿里巴巴", "阿里", "蚂蚁集团", "蚂蚁金服",
    "腾讯", "字节跳动", "抖音", "tiktok", "今日头条",
    "百度", "京东", "美团", "拼多多", "滴滴", "滴滴出行",
    "网易", "小米", "快手", "携程", "三六零", "360",
    "新浪", "微博", "搜狐", "哔哩哔哩", "b站", "bilibili",
    "小红书", "得物", "贝壳找房", "链家", "58同城",  # 58同城作为数据源但公司本身排除
    "蚂蚁", "菜鸟", "盒马", "饿了么", "飞猪", "钉钉",
    "爱奇艺", "优酷", "腾讯视频", "腾讯云", "阿里云", "百度智能云",
    "商汤", "旷视", "依图", "云从", "第四范式",
    "科大讯飞",  # 用户对标大厂
    "联想", "中兴",  # 大型科技企业
    "oppo", "vivo", "荣耀",
    "米哈游", "完美世界", "三七互娱", "巨人网络",
]


# ============ 成都企业库 ============
# 国企/央企/外资/合资/研究所/学校（学校归为研究所），排除互联网大厂
# round字段不再按类型分配，改为均匀分配到5轮

CHENGDU_COMPANIES = [
    # === 央企 ===
    {"name": "中国电建集团成都勘测设计研究院", "type": "央企", "career_url": "https://www.powerchina-cdc.com/"},
    {"name": "中国核动力研究设计院", "type": "央企", "career_url": "https://www.npic.ac.cn/"},
    {"name": "东方电气集团", "type": "央企", "career_url": "https://www.dec.ltd/cn/careers"},
    {"name": "中国建筑西南设计研究院", "type": "央企", "career_url": "https://www.csweadi.com/"},
    {"name": "中国节能环保集团", "type": "央企", "career_url": "https://www.cecic.com.cn/"},
    {"name": "中国电科网络空间安全研究院", "type": "央企", "career_url": "https://www.cetc.com.cn/"},
    {"name": "中国电子科技集团第十研究所", "type": "央企", "career_url": "https://www.swiee.com/"},
    {"name": "中国电子科技集团第二十九研究所", "type": "央企", "career_url": "https://www.swiee.com/"},
    {"name": "中科院成都计算机应用研究所", "type": "央企", "career_url": "http://www.casit.com.cn/"},
    {"name": "中国航空工业集团成都飞机设计研究所", "type": "央企", "career_url": "http://www.cac.com.cn/"},

    # === 国企 ===
    {"name": "国家电网四川省电力公司", "type": "国企", "career_url": "https://www.sgcc.com.cn/"},
    {"name": "中国电信四川分公司", "type": "国企", "career_url": "https://www.chinatelecom.com.cn/careers/"},
    {"name": "中国移动四川分公司", "type": "国企", "career_url": "https://hr.10086.cn/"},
    {"name": "四川长虹电器股份有限公司", "type": "国企", "career_url": "https://www.changhong.com.cn/"},
    {"name": "蜀道投资集团", "type": "国企", "career_url": "https://www.sdic.com.cn/"},
    {"name": "四川发展控股", "type": "国企", "career_url": "http://www.sdzk.cn/"},
    {"name": "成都环境投资集团", "type": "国企", "career_url": "https://www.cdeg.com.cn/"},
    {"name": "成都轨道交通集团", "type": "国企", "career_url": "https://www.cdmetro.cn/"},

    # === 外资 ===
    {"name": "英特尔产品(成都)", "type": "外资", "career_url": "https://www.intel.com/content/www/us/en/jobs/jobs-at-intel.html"},
    {"name": "IBM成都", "type": "外资", "career_url": "https://www.ibm.com/careers"},
    {"name": "西门子成都", "type": "外资", "career_url": "https://new.siemens.com/cn/zh/company/jobs.html"},
    {"name": "微软成都", "type": "外资", "career_url": "https://careers.microsoft.com/"},
    {"name": "亚马逊成都", "type": "外资", "career_url": "https://www.amazon.jobs/"},
    {"name": "SAP成都", "type": "外资", "career_url": "https://jobs.sap.com/"},
    {"name": "Dell成都", "type": "外资", "career_url": "https://www.dell.com/learn/cn/zh/cncorp1/careers"},
    {"name": "Nokia成都", "type": "外资", "career_url": "https://www.nokia.com/about-us/careers/"},
    {"name": "Ericsson成都", "type": "外资", "career_url": "https://www.ericsson.com/en/careers"},
    {"name": "Thoughtworks成都", "type": "外资", "career_url": "https://www.thoughtworks.com/careers"},

    # === 合资 ===
    {"name": "一汽-大众成都", "type": "合资", "career_url": "https://www.faw-vw.com/"},
    {"name": "中科创达软件", "type": "合资", "career_url": "https://www.thundercomm.com/careers"},
    {"name": "外企德科", "type": "合资", "career_url": "https://www.fescoadecco.com/"},

    # === 学校（归类为研究所） ===
    {"name": "成都信息工程大学", "type": "学校", "career_url": "https://jy.cuit.edu.cn/"},
    {"name": "四川大学", "type": "学校", "career_url": "https://jwc.scu.edu.cn/"},
    {"name": "电子科技大学", "type": "学校", "career_url": "https://career.uestc.edu.cn/"},
    {"name": "西南交通大学", "type": "学校", "career_url": "https://jiuye.swjtu.edu.cn/"},
    {"name": "成都理工大学", "type": "学校", "career_url": "https://jy.cdut.edu.cn/"},
    {"name": "四川师范大学", "type": "学校", "career_url": "https://jy.sicnu.edu.cn/"},
]

# 企业均匀分配到5轮（不按类型分）
for _i, _c in enumerate(CHENGDU_COMPANIES):
    _c['round'] = (_i % 5) + 1


# ============ 浏览器配置（反检测） ============

def create_browser_context(playwright):
    browser = playwright.chromium.launch(
        headless=True,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--disable-features=IsolateOrigins,site-per-process',
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
        ]
    )
    context = browser.new_context(
        user_agent=UA,
        viewport={"width": 1920, "height": 1080},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    )
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
        window.chrome = { runtime: {} };
    """)
    return browser, context


def is_excluded_job(title, tags="", company=""):
    """检查是否应排除：兼职/校招/博士/互联网大厂/年龄限制/党员要求/排除行业"""
    text = (title + " " + tags).lower()
    # 兼职/校招/实习
    for kw in EXCLUDE_KEYWORDS:
        if kw in text:
            return True
    # 博士学历
    for kw in PHD_EXCLUDE_KEYWORDS:
        if kw.lower() in text:
            return True
    # 年龄限制
    for kw in AGE_EXCLUDE_KEYWORDS:
        if kw in text:
            return True
    # 党员要求
    for kw in PARTY_EXCLUDE_KEYWORDS:
        if kw in text:
            return True
    # 排除行业（检查标题、标签、公司名）
    all_text = (title + " " + tags + " " + company).lower()
    for kw in EXCLUDED_INDUSTRIES:
        if kw.lower() in all_text:
            return True
    # 互联网大厂
    company_lower = (company or "").lower()
    for giant in INTERNET_GIANTS:
        if giant.lower() in company_lower:
            return True
    return False


def parse_salary_to_monthly(salary_text):
    """将薪资文本解析为月最低薪资（元），无法解析返回None"""
    if not salary_text:
        return None
    text = salary_text.replace(',', '').replace(' ', '').replace('，', '')

    # 检测时间单位
    is_annual = '年' in text or 'year' in text.lower()
    is_daily = '天' in text or '日薪' in text or '/天' in text or '每天' in text
    is_hourly = '时' in text or 'hour' in text.lower()

    # 用正则提取"数字+单位"对，每个数字绑定自己的单位
    # 匹配: 12K, 1.2万, 8千, 12000, 12k, 1.5w 等
    pairs = re.findall(r'(\d+\.?\d*)\s*(千|万|k|K|w|W)?', text)
    if not pairs:
        return None

    first_num = float(pairs[0][0])
    first_unit = pairs[0][1] if len(pairs[0]) > 1 and pairs[0][1] else ''

    # 若第一个数字没有显式单位，从全文推断
    if not first_unit:
        if '万' in text or 'w' in text.lower():
            first_unit = '万'
        elif '千' in text:
            first_unit = '千'
        elif 'k' in text.lower():
            first_unit = 'k'

    # 转换为月薪
    if first_unit in ('万', 'w', 'W'):
        base = first_num * 10000
        min_monthly = base / 12 if is_annual else base
    elif first_unit in ('k', 'K', '千'):
        base = first_num * 1000
        min_monthly = base / 12 if is_annual else base
    else:
        # 纯数字
        if is_annual:
            min_monthly = first_num / 12
        elif is_daily:
            min_monthly = first_num * 22  # 约22个工作日
        elif is_hourly:
            min_monthly = first_num * 8 * 22  # 8小时×22天
        else:
            min_monthly = first_num

    return min_monthly


def passes_salary_filter(salary_text, company_type=None):
    """薪资预过滤：普通类别≥MIN_SALARY元，其他类别≥OTHER_MIN_SALARY元"""
    if not salary_text or '面议' in salary_text:
        return True
    # 根据公司类型决定薪资门槛
    normalized = _normalize_type(company_type) if company_type else ''
    min_salary = OTHER_MIN_SALARY if normalized == '其他' else MIN_SALARY
    min_monthly = parse_salary_to_monthly(salary_text)
    if min_monthly is None:
        return True  # 无法解析，保留让LLM判断
    return min_monthly >= min_salary


# ============ 51job 抓取 ============

def scrape_51job(playwright, keyword):
    print(f"  [51job] 搜索: {keyword}")
    jobs = []
    browser, context = create_browser_context(playwright)
    page = context.new_page()
    try:
        url = f"https://we.51job.com/pc/search?keyword={urllib.parse.quote(keyword)}&jobArea={JOB51_CITY}&sortType=0"
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        time.sleep(random.uniform(3, 5))
        # 关闭登录弹窗
        try:
            page.evaluate("""() => {
                document.querySelectorAll('[class*="close"], [class*="mask"], .layer-close').forEach(e => e.click());
            }""")
        except:
            pass
        for _ in range(3):
            page.mouse.wheel(0, random.randint(300, 800))
            time.sleep(0.5)

        raw_jobs = page.evaluate("""() => {
            try {
                if (window.__SEARCH_RESULT__) {
                    const result = window.__SEARCH_RESULT__;
                    const jobs = result.engine_search_result || result.joblist || [];
                    return jobs.map(j => {
                        let link = j.job_href || '';
                        if (link && link.startsWith('//')) link = 'https:' + link;
                        if (link && !link.startsWith('http')) link = '';
                        return {
                            title: j.job_name || '', company: j.company_name || '',
                            salary: j.providesalary_text || j.salary || '',
                            location: j.workarea_text || (j.attribute_text && j.attribute_text[0]) || '成都',
                            tags: (j.attribute_text || []).join(' | ') + ' | ' + (j.jobwelf || ''),
                            link: link, source: '51job'
                        };
                    });
                }
            } catch(e) {}
            return null;
        }""")

        if not raw_jobs:
            raw_jobs = page.evaluate("""() => {
                const jobs = [];
                const sels = ['.j_joblist', '.el', '[class*="joblist"]', '[class*="job-item"]', '.t1'];
                let cards = [];
                for (const s of sels) { cards = document.querySelectorAll(s); if (cards.length) break; }
                if (!cards.length) {
                    document.querySelectorAll('a[href*="jobs.51job.com"], a[href*="/job/"]').forEach(a => {
                        const t = a.textContent.trim();
                        if (t && t.length > 2 && t.length < 50)
                            jobs.push({title:t,company:'',salary:'',location:'成都',tags:'',link:a.href,source:'51job'});
                    });
                    return jobs;
                }
                cards.forEach(c => {
                    const g = s => { const e = c.querySelector(s); return e ? e.textContent.trim() : ''; };
                    const le = c.querySelector('a[href*="jobs.51job.com"], a[href*="/job/"], .jname a, a.t1');
                    if (g('.jname') || g('.cname'))
                        jobs.push({title:g('.jname, .t1 a, [class*="jname"]'),company:g('.cname, [class*="cname"]'),
                            salary:g('.sal, [class*="sal"]'),location:g('.info .name, [class*="area"]')||'成都',
                            tags:c.textContent.trim().substring(0,300),link:le?le.href:'',source:'51job'});
                });
                return jobs;
            }""")

        seen = set()
        for job in (raw_jobs or []):
            if is_excluded_job(job.get('title', ''), job.get('tags', ''), job.get('company', '')):
                continue
            if not passes_salary_filter(job.get('salary', '')):
                continue
            key = f"{job.get('title','')}_{job.get('company','')}"
            if key not in seen and (job.get('title') or job.get('company')):
                seen.add(key)
                jobs.append(job)
        print(f"    提取到 {len(jobs)} 个职位（已排除兼职/校招/大厂/年龄/党员/低薪）")
    except Exception as e:
        print(f"    [ERROR] {e}")
    finally:
        browser.close()
    return jobs


# ============ 猎聘抓取 ============

def scrape_liepin_api(keyword):
    print(f"  [猎聘API] 搜索: {keyword}")
    jobs = []
    try:
        api_url = "https://api-c.liepin.com/api/com.liepin.searchfront4c.pc-search-job"
        headers = {
            "User-Agent": UA,
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.liepin.com",
            "Referer": f"https://www.liepin.com/zhaopin/?key={urllib.parse.quote(keyword)}&dqs={LIEPIN_CITY}",
            "X-Requested-With": "XMLHttpRequest",
        }
        payload = {"data": {"mainSearchPcConditionForm": {
            "city": LIEPIN_CITY, "dq": LIEPIN_CITY, "currentPage": "0", "pageSize": 40,
            "key": keyword, "workYearCode": "0", "industry": "", "salary": "",
            "eduLevel": "", "function": "", "sortType": "0",
        }, "passThroughForm": {"scene": "init", "ckId": "", "skId": "", "fkId": ""}}}
        resp = requests.post(api_url, json=payload, headers=headers, timeout=20)
        if resp.status_code == 200:
            for card in resp.json().get("data", {}).get("data", {}).get("jobCardList", []):
                ji = card.get("job", {})
                ci = card.get("comp", {})
                title = ji.get("title", "")
                tags = " | ".join(ji.get("labels", [])) + f" | {ci.get('compScale','')} | {ci.get('industryName','')}"
                if is_excluded_job(title, tags, ci.get("compName", "")):
                    continue
                salary = ji.get("salary", "")
                if not passes_salary_filter(salary):
                    continue
                link = ji.get("link", "")
                if link and not link.startswith("http"):
                    link = "https://www.liepin.com" + link
                # 回退：如果link为空，用jobId构造URL
                if not link:
                    job_id = ji.get("jobId", "") or ji.get("id", "")
                    if job_id:
                        link = f"https://www.liepin.com/job/{job_id}.shtml"
                hr_activity = ""
                recruiter = card.get("recruiter", {})
                if recruiter:
                    hr_activity = recruiter.get("lastLogin", "") or recruiter.get("activeStatus", "") or ""
                jobs.append({"title": title, "company": ci.get("compName", ""), "salary": salary,
                    "location": ji.get("dq", "成都"), "tags": tags, "link": link, "source": "猎聘",
                    "hr_activity": hr_activity})
            print(f"    [API] 返回 {len(jobs)} 个职位")
        else:
            print(f"    [WARN] API {resp.status_code}")
    except Exception as e:
        print(f"    [WARN] API异常: {e}")
    return jobs


def scrape_liepin_browser(playwright, keyword):
    print(f"  [猎聘浏览器] 搜索: {keyword}")
    jobs = []
    browser, context = create_browser_context(playwright)
    page = context.new_page()
    try:
        url = f"https://www.liepin.com/zhaopin/?key={urllib.parse.quote(keyword)}&dqs={LIEPIN_CITY}"
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        time.sleep(random.uniform(4, 6))
        for _ in range(4):
            page.mouse.wheel(0, random.randint(300, 800))
            time.sleep(random.uniform(0.5, 1.5))

        raw_jobs = page.evaluate("""() => {
            const jobs = [];
            const sels = ['.job-list-item','[class*="job-item"]','[class*="job-card"]','[data-job-id]','.job-card-wrap'];
            let cards = [];
            for (const s of sels) { cards = document.querySelectorAll(s); if (cards.length) break; }
            if (!cards.length) {
                document.querySelectorAll('a[href*="/job/"]').forEach(a => {
                    const t = a.textContent.trim();
                    if (t && t.length > 2 && t.length < 60)
                        jobs.push({title:t,company:'',salary:'',location:'成都',tags:'',link:a.href,source:'猎聘'});
                });
                return jobs;
            }
            cards.forEach(c => {
                const g = s => { const e = c.querySelector(s); return e ? e.textContent.trim() : ''; };
                const le = c.querySelector('a[href*="/job/"], a[href*="liepin.com/job"]');
                if (g('.job-title') || g('[class*="title"]'))
                    jobs.push({title:g('.job-title,[class*="title"],h3'),company:g('[class*="company"],[class*="comp"]'),
                        salary:g('[class*="salary"]'),location:g('[class*="area"]')||'成都',
                        tags:c.textContent.trim().substring(0,500),link:le?le.href:'',source:'猎聘'});
            });
            return jobs;
        }""")
        for job in (raw_jobs or []):
            if not is_excluded_job(job.get('title', ''), job.get('tags', ''), job.get('company', '')) and passes_salary_filter(job.get('salary', '')):
                jobs.append(job)
        print(f"    [DOM] 提取到 {len(jobs)} 个职位")
    except Exception as e:
        print(f"    [ERROR] {e}")
    finally:
        browser.close()
    return jobs


def scrape_liepin(playwright, keyword):
    jobs = scrape_liepin_api(keyword)
    if not jobs:
        jobs = scrape_liepin_browser(playwright, keyword)
    return jobs


# ============ 智联招聘抓取 ============

def scrape_zhaopin(playwright, keyword):
    """智联招聘"""
    print(f"  [智联招聘] 搜索: {keyword}")
    jobs = []
    browser, context = create_browser_context(playwright)
    page = context.new_page()
    try:
        url = f"https://sou.zhaopin.com/?jl={ZHAOPIN_CITY}&kw={urllib.parse.quote(keyword)}&p=1"
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        time.sleep(random.uniform(4, 6))
        # 关闭登录弹窗
        try:
            page.evaluate("""() => {
                document.querySelectorAll('[class*="close"], [class*="mask"], .modal-close').forEach(e => e.click());
            }""")
        except:
            pass
        for _ in range(3):
            page.mouse.wheel(0, random.randint(300, 600))
            time.sleep(random.uniform(0.5, 1))

        raw_jobs = page.evaluate("""() => {
            const jobs = [];
            const sels = ['.joblist-box__item', '.JobList .jobcard', '[class*="jobcard"]',
                          '.sou-job-item', '.joblist-box li', '[class*="job-item"]'];
            let cards = [];
            for (const s of sels) { cards = document.querySelectorAll(s); if (cards.length) break; }
            if (!cards.length) {
                document.querySelectorAll('a[href*="/jobs/"], a[href*="zhaopin.com/job"]').forEach(a => {
                    const t = a.textContent.trim();
                    if (t && t.length > 2 && t.length < 60)
                        jobs.push({title:t,company:'',salary:'',location:'成都',tags:'',link:a.href,source:'智联招聘'});
                });
                return jobs;
            }
            cards.forEach(c => {
                const g = s => { const e = c.querySelector(s); return e ? e.textContent.trim() : ''; };
                const le = c.querySelector('a[href*="/jobs/"], a[href*="zhaopin.com/job"]');
                const title = g('.jobinfo__name, .job-name, [class*="job-title"], [class*="name"], h3');
                const company = g('.company__name, .company-name, [class*="company"], [class*="comp"]');
                const salary = g('.jobinfo__salary, .salary, [class*="salary"]');
                const loc = g('.jobinfo__location, .area, [class*="area"], [class*="location"]') || '成都';
                if (title || company)
                    jobs.push({title:title||'', company:company||'', salary:salary||'',
                        location:loc, tags:c.textContent.trim().substring(0,500),
                        link:le?le.href:'', source:'智联招聘'});
            });
            return jobs;
        }""")
        for job in (raw_jobs or []):
            if not is_excluded_job(job.get('title', ''), job.get('tags', ''), job.get('company', '')) and passes_salary_filter(job.get('salary', '')):
                jobs.append(job)
        print(f"    提取到 {len(jobs)} 个职位")
    except Exception as e:
        print(f"    [ERROR] {e}")
    finally:
        browser.close()
    return jobs


# ============ Boss直聘抓取 ============

def scrape_boss(playwright, keyword):
    print(f"  [Boss直聘] 搜索: {keyword}")
    jobs = []
    browser, context = create_browser_context(playwright)
    page = context.new_page()
    try:
        url = f"https://www.zhipin.com/web/geek/job?query={urllib.parse.quote(keyword)}&city={BOSS_CITY}"
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        time.sleep(random.uniform(5, 8))
        # 关闭登录弹窗
        try:
            page.evaluate("""() => {
                document.querySelectorAll('[class*="close"], .modal-close, [class*="dialog-close"]').forEach(e => e.click());
            }""")
        except:
            pass
        for _ in range(3):
            page.mouse.wheel(0, random.randint(300, 600))
            time.sleep(random.uniform(0.5, 1))

        raw_jobs = page.evaluate("""() => {
            const jobs = [];
            const sels = ['.job-card-wrapper', '.job-card-body', '[class*="job-card"]', '.search-job-result li'];
            let cards = [];
            for (const s of sels) { cards = document.querySelectorAll(s); if (cards.length) break; }
            if (!cards.length) {
                document.querySelectorAll('a[href*="/job_detail/"]').forEach(a => {
                    const t = a.textContent.trim();
                    if (t && t.length > 2 && t.length < 60)
                        jobs.push({title:t,company:'',salary:'',location:'成都',tags:'',link:a.href,source:'Boss直聘',hr_activity:''});
                });
                return jobs;
            }
            cards.forEach(c => {
                const g = s => { const e = c.querySelector(s); return e ? e.textContent.trim() : ''; };
                const le = c.querySelector('a[href*="/job_detail/"], a[href*="zhipin.com/job"]');
                const hrEl = c.querySelector('.boss-name, [class*="boss"], [class*="hr"], [class*="recruiter"]');
                const hrText = hrEl ? hrEl.textContent.trim() : '';
                if (g('.job-name') || g('[class*="job-title"]'))
                    jobs.push({title:g('.job-name,[class*="job-title"],h3'),company:g('.company-name,[class*="company"]'),
                        salary:g('.salary,[class*="salary"]'),location:g('.job-area,[class*="area"]')||'成都',
                        tags:c.textContent.trim().substring(0,500),link:le?le.href:'',source:'Boss直聘',
                        hr_activity:hrText});
            });
            return jobs;
        }""")
        for job in (raw_jobs or []):
            if not is_excluded_job(job.get('title', ''), job.get('tags', ''), job.get('company', '')) and passes_salary_filter(job.get('salary', '')):
                jobs.append(job)
        print(f"    提取到 {len(jobs)} 个职位")
    except Exception as e:
        print(f"    [ERROR] {e}")
    finally:
        browser.close()
    return jobs


# ============ 58同城抓取 ============

def scrape_58(playwright, keyword):
    print(f"  [58同城] 搜索: {keyword}")
    jobs = []
    browser, context = create_browser_context(playwright)
    page = context.new_page()
    try:
        url = f"https://cd.58.com/job/?key={urllib.parse.quote(keyword)}&final=1&jump=1"
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        time.sleep(random.uniform(3, 5))
        for _ in range(3):
            page.mouse.wheel(0, random.randint(300, 600))
            time.sleep(0.5)

        raw_jobs = page.evaluate("""() => {
            const jobs = [];
            const sels = ['.job-item', '.list-item', '[class*="job-list"] li', '.infolist .info', '.j_zw_list li'];
            let cards = [];
            for (const s of sels) { cards = document.querySelectorAll(s); if (cards.length) break; }
            if (!cards.length) {
                document.querySelectorAll('a[href*="/job/"]').forEach(a => {
                    const t = a.textContent.trim();
                    if (t && t.length > 2 && t.length < 60)
                        jobs.push({title:t,company:'',salary:'',location:'成都',tags:'',link:a.href,source:'58同城'});
                });
                return jobs;
            }
            cards.forEach(c => {
                const g = s => { const e = c.querySelector(s); return e ? e.textContent.trim() : ''; };
                const le = c.querySelector('a[href*="/job/"], a[href*="/info-"], a[href*="58.com/job"]');
                if (g('.job_name') || g('[class*="title"]'))
                    jobs.push({title:g('.job_name,[class*="title"],h3'),company:g('.company_name,[class*="company"]'),
                        salary:g('.salary,[class*="salary"]'),location:g('.address,[class*="area"]')||'成都',
                        tags:c.textContent.trim().substring(0,500),link:le?le.href:'',source:'58同城'});
            });
            return jobs;
        }""")
        for job in (raw_jobs or []):
            if not is_excluded_job(job.get('title', ''), job.get('tags', ''), job.get('company', '')) and passes_salary_filter(job.get('salary', '')):
                jobs.append(job)
        print(f"    提取到 {len(jobs)} 个职位")
    except Exception as e:
        print(f"    [ERROR] {e}")
    finally:
        browser.close()
    return jobs


# ============ 鱼泡网抓取 ============

def scrape_yupao(playwright, keyword):
    print(f"  [鱼泡网] 搜索: {keyword}")
    jobs = []
    browser, context = create_browser_context(playwright)
    page = context.new_page()
    try:
        url = f"https://www.yupao.com/job/search?keyword={urllib.parse.quote(keyword)}&cityCode=510100"
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        time.sleep(random.uniform(3, 5))
        for _ in range(2):
            page.mouse.wheel(0, random.randint(300, 600))
            time.sleep(0.5)

        raw_jobs = page.evaluate("""() => {
            const jobs = [];
            const sels = ['.job-item', '.job-card', '[class*="job-list"] li', '.list-item'];
            let cards = [];
            for (const s of sels) { cards = document.querySelectorAll(s); if (cards.length) break; }
            if (!cards.length) {
                document.querySelectorAll('a[href*="/job/"]').forEach(a => {
                    const t = a.textContent.trim();
                    if (t && t.length > 2 && t.length < 60)
                        jobs.push({title:t,company:'',salary:'',location:'成都',tags:'',link:a.href,source:'鱼泡网'});
                });
                return jobs;
            }
            cards.forEach(c => {
                const g = s => { const e = c.querySelector(s); return e ? e.textContent.trim() : ''; };
                const le = c.querySelector('a[href*="/job/"], a[href*="yupao.com/job"]');
                if (g('.title') || g('[class*="job-name"]'))
                    jobs.push({title:g('.title,[class*="job-name"]'),company:g('.company,[class*="company"]'),
                        salary:g('.salary,[class*="salary"]'),location:g('.area,[class*="city"]')||'成都',
                        tags:c.textContent.trim().substring(0,500),link:le?le.href:'',source:'鱼泡网'});
            });
            return jobs;
        }""")
        for job in (raw_jobs or []):
            if not is_excluded_job(job.get('title', ''), job.get('tags', ''), job.get('company', '')) and passes_salary_filter(job.get('salary', '')):
                jobs.append(job)
        print(f"    提取到 {len(jobs)} 个职位")
    except Exception as e:
        print(f"    [ERROR] {e}")
    finally:
        browser.close()
    return jobs


# ============ 成都信息工程大学就业网抓取 ============

def scrape_cuit(playwright, keyword):
    """成都信息工程大学就业信息网"""
    print(f"  [成信大就业网] 搜索: {keyword}")
    jobs = []
    browser, context = create_browser_context(playwright)
    page = context.new_page()
    try:
        url = f"https://jy.cuit.edu.cn/recruitment/search?keyword={urllib.parse.quote(keyword)}"
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(random.uniform(3, 5))

        raw_jobs = page.evaluate("""() => {
            const jobs = [];
            const sels = ['.job-item', '.recruit-item', '.list-item', '[class*="job"] li',
                          '.infolist li', '.job_list li', '[class*="recruit"] li'];
            let cards = [];
            for (const s of sels) { cards = document.querySelectorAll(s); if (cards.length) break; }
            if (!cards.length) {
                document.querySelectorAll('a[href*="/recruitment/view/"], a[href*="/job/"]').forEach(a => {
                    const t = a.textContent.trim();
                    if (t && t.length > 2 && t.length < 80)
                        jobs.push({title:t,company:'',salary:'',location:'成都',tags:'',link:a.href,source:'成信大就业网'});
                });
                return jobs;
            }
            cards.forEach(c => {
                const g = s => { const e = c.querySelector(s); return e ? e.textContent.trim() : ''; };
                const le = c.querySelector('a[href*="/recruitment/view/"], a[href*="/job/"]');
                const title = g('.job-title, .title, [class*="title"], h3, h4');
                const company = g('.company-name, .company, [class*="company"]');
                if (title || company)
                    jobs.push({title:title||'', company:company||'', salary:g('.salary, [class*="salary"]'),
                        location:'成都', tags:c.textContent.trim().substring(0,500),
                        link:le?le.href:'', source:'成信大就业网'});
            });
            return jobs;
        }""")
        for job in (raw_jobs or []):
            if not is_excluded_job(job.get('title', ''), job.get('tags', ''), job.get('company', '')):
                jobs.append(job)
        print(f"    提取到 {len(jobs)} 个职位")
    except Exception as e:
        print(f"    [ERROR] {e}")
    finally:
        browser.close()
    return jobs


# ============ 企业官网招聘页抓取 ============

def scrape_company_career(context, company):
    name = company['name']
    ctype = company['type']
    career_url = company['career_url']
    print(f"  [企业官网] {name} ({ctype})")

    jobs = []
    page = context.new_page()
    try:
        page.goto(career_url, timeout=20000, wait_until="domcontentloaded")
        time.sleep(random.uniform(1, 2))

        raw_jobs = page.evaluate("""(companyName) => {
            const jobs = [];
            // 辅助函数：判断链接是否有效
            const isValidLink = (href) => {
                if (!href) return false;
                if (href.startsWith('javascript:')) return false;
                if (href === '#' || href.includes('void(0)')) return false;
                if (!href.startsWith('http')) return false;
                return true;
            };
            const sels = ['.job-item', '.position-item', '.job-card', '[class*="job-list"] li',
                '.recruit-list li', '.job_content', '[class*="position"]', '[class*="recruit"]',
                '.list-item', '.job-list-item', '[data-job-id]'];
            let cards = [];
            for (const s of sels) { cards = document.querySelectorAll(s); if (cards.length) break; }
            if (cards.length === 0) {
                document.querySelectorAll('a').forEach(a => {
                    const text = a.textContent.trim();
                    const href = a.href;
                    if (isValidLink(href) && text.length > 2 && text.length < 80 &&
                        (text.includes('工程师') || text.includes('专员') || text.includes('经理') ||
                         text.includes('分析师') || text.includes('研究员') || text.includes('设计') ||
                         text.includes('算法') || text.includes('开发'))) {
                        jobs.push({title:text, company:companyName, salary:'', location:'成都',
                            tags:'', link:href, source:'企业官网'});
                    }
                });
                return jobs;
            }
            cards.forEach(c => {
                const g = s => { const e = c.querySelector(s); return e ? e.textContent.trim() : ''; };
                // 优先选择包含job/position/recruit关键词的链接
                const le = c.querySelector('a[href*="job"], a[href*="position"], a[href*="recruit"], a[href*="detail"], a[href*="view"]');
                const link = le ? le.href : '';
                const title = g('.job-name, .job-title, .position-name, [class*="title"], h3, h4');
                if (title && isValidLink(link))
                    jobs.push({title:title, company:companyName, salary:g('.salary, [class*="salary"]'),
                        location:g('.area, [class*="city"]')||'成都',
                        tags:c.textContent.trim().substring(0,500), link:link,
                        source:'企业官网'});
            });
            return jobs;
        }""", name)

        for job in (raw_jobs or []):
            job['company_type'] = ctype
            if not is_excluded_job(job.get('title', ''), job.get('tags', ''), job.get('company', '')):
                jobs.append(job)
        print(f"    提取到 {len(jobs)} 个职位")
    except Exception as e:
        print(f"    [ERROR] {e}")
    finally:
        page.close()
    return jobs


# ============ 轮次搜索 ============

def scrape_round(round_num):
    """执行指定轮次的搜索"""
    keywords = KEYWORD_BATCHES.get(round_num, [])
    all_jobs = []
    seen = set()

    def add_jobs(jobs_list):
        for j in jobs_list:
            # 规范化链接
            if j.get('link'):
                j['link'] = _normalize_url(j['link'])
            key = f"{j.get('title','')}_{j.get('company','')}"
            if key not in seen and (j.get('title') or j.get('company')):
                seen.add(key)
                all_jobs.append(j)

    with sync_playwright() as p:
        for i, keyword in enumerate(keywords, 1):
            print(f"\n[轮次{round_num} - 关键词 {i}/{len(keywords)}] {keyword}")

            # 每轮都搜索主要平台
            add_jobs(scrape_51job(p, keyword))
            add_jobs(scrape_liepin(p, keyword))
            add_jobs(scrape_zhaopin(p, keyword))

            # Boss/58 用精简关键词
            if keyword in LITE_KEYWORDS:
                add_jobs(scrape_boss(p, keyword))
                add_jobs(scrape_58(p, keyword))
                add_jobs(scrape_yupao(p, keyword))

            time.sleep(random.uniform(1, 2))

        # 成信大就业网（第5轮搜索）
        if round_num == 5:
            print(f"\n[轮次5 - 成信大就业网]")
            for kw in ["气象", "环境", "大模型", "AI"]:
                add_jobs(scrape_cuit(p, kw))
                time.sleep(random.uniform(0.5, 1))

        # 企业官网（按轮次分配）
        round_companies = [c for c in CHENGDU_COMPANIES if c.get('round', 5) == round_num]
        if round_companies:
            print(f"\n[轮次{round_num} - 企业官网 ({len(round_companies)}家)]")
            browser2, context2 = create_browser_context(p)
            for i, company in enumerate(round_companies, 1):
                if i % 3 == 0:
                    print(f"  进度: {i}/{len(round_companies)}")
                add_jobs(scrape_company_career(context2, company))
                time.sleep(random.uniform(0.3, 0.8))
            browser2.close()

    print(f"\n轮次{round_num}去重后: {len(all_jobs)} 个职位")
    return all_jobs


# ============ 中间结果存储 ============

def save_round_results(round_num, jobs, date_str):
    """保存轮次搜索结果"""
    os.makedirs(RAW_DIR, exist_ok=True)
    filepath = os.path.join(RAW_DIR, f"{date_str}_r{round_num}.json")
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    print(f"  已保存轮次{round_num}结果: {filepath} ({len(jobs)}个职位)")


def load_all_round_results(date_str, current_round):
    """加载所有轮次的结果"""
    all_jobs = []
    seen = set()
    for r in range(1, current_round + 1):
        filepath = os.path.join(RAW_DIR, f"{date_str}_r{r}.json")
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    jobs = json.load(f)
                for j in jobs:
                    # 规范化链接
                    if j.get('link'):
                        j['link'] = _normalize_url(j['link'])
                    key = f"{j.get('title','')}_{j.get('company','')}"
                    if key not in seen:
                        seen.add(key)
                        all_jobs.append(j)
                print(f"  加载轮次{r}: {len(jobs)}个职位")
            except Exception as e:
                print(f"  [WARN] 加载轮次{r}失败: {e}")
    print(f"  合并去重后: {len(all_jobs)}个职位")
    return all_jobs


def cleanup_raw_files(date_str):
    """清理中间结果文件"""
    for r in range(1, 6):
        filepath = os.path.join(RAW_DIR, f"{date_str}_r{r}.json")
        if os.path.exists(filepath):
            os.remove(filepath)
            print(f"  清理: {filepath}")


# ============ GLM-4-Flash 智能筛选 ============

def filter_jobs_with_llm(jobs):
    """使用 GLM-4-Flash 智能筛选，返回结构化数据"""
    if not jobs:
        return []

    BATCH_SIZE = 25
    all_filtered = []

    for batch_start in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[batch_start:batch_start + BATCH_SIZE]
        print(f"  筛选批次 {batch_start // BATCH_SIZE + 1}（{len(batch)} 个）...")

        # 构建原始数据索引，用于多级匹配恢复真实链接
        # 1. idx索引（主匹配）
        # 2. 公司名+职位标题索引（回退匹配）
        # 3. URL索引（验证LLM返回的URL是否真实）
        original_by_idx = {}       # idx -> original job
        original_by_comp_title = {}  # (company, title) -> original job
        original_urls = set()      # 所有原始链接集合

        for idx, job in enumerate(batch, 1):
            original_by_idx[idx] = job
            comp = (job.get('company', '') or '').strip().lower()
            title = (job.get('title', '') or '').strip().lower()
            if comp and title:
                original_by_comp_title[(comp, title)] = job
            link = job.get('link', '')
            if link and link.startswith('http'):
                original_urls.add(link)

        jobs_text = ""
        for idx, job in enumerate(batch, 1):
            jobs_text += f"\n[{idx}] 职位: {job.get('title', '')}\n"
            jobs_text += f"   公司: {job.get('company', '')}"
            if job.get('company_type'):
                jobs_text += f"（{job['company_type']}）"
            jobs_text += "\n"
            jobs_text += f"   薪资: {job.get('salary', '')}\n"
            jobs_text += f"   地点: {job.get('location', '')}\n"
            jobs_text += f"   标签/要求: {job.get('tags', '')[:300]}\n"
            jobs_text += f"   来源: {job.get('source', '')}\n"
            if job.get('link'):
                jobs_text += f"   链接: {job['link']}\n"
            if job.get('hr_activity'):
                jobs_text += f"   HR活跃: {job['hr_activity']}\n"

        prompt = f"""以下是搜索到的成都地区职位信息，请严格按以下条件筛选：

【筛选条件 - 必须全部满足】
1. 全职社招：必须是全职+社招。排除兼职、校招、校园招聘、应届生、实习。
2. 领域匹配（满足任一）：
   - 气象/大气科学/气候/天气预报/气象算法
   - 环境科学/环境工程/生态环境/环保/水处理
   - 大模型/LLM/AI agent/人工智能/机器学习/深度学习/NLP
   - 职位名称不一定包含关键词，只要职责描述或技能要求中涉及上述领域就保留。
3. 专业要求：
   - 如果明确要求了学科专业，要求中必须包含气象/大气科学/环境相关专业，否则排除。
   - 如果没有明确限制专业（专业不限/未提及/计算机相关），则保留。
4. 公司类别分类规则（重要）：
   - 国企→标注"国企"
   - 央企→标注"央企"
   - 外资/外企→标注"外资"
   - 合资/中外合资→标注"合资"
   - 研究所/研究院/设计院→标注"研究所"
   - 事业单位→标注"研究所"
   - 大学/学院/学校/院校→标注"研究所"
   - 以上类型无法判断但非互联网大厂→标注"待确认"
   - 其他所有类型（民营/私营/股份制等非上述类型）→标注"其他"
   - 排除民营互联网大厂：华为、阿里巴巴、腾讯、字节跳动、百度、京东、美团、拼多多、滴滴、网易、小米、快手、携程、新浪、微博、哔哩哔哩、小红书、科大讯飞、商汤、联想、中兴、米哈游等大型互联网/科技公司及其子公司。
5. 薪资门槛（按公司类型区分）：
   - 国企/央企/外资/合资/研究所/待确认：月薪不低于12000元，"面议"保留，低于1.2万/月排除。
   - 其他类别：月薪不低于18000元，"面议"保留，低于1.8万/月排除。
6. 排除年龄限制：如果招聘要求中明确要求年龄在35岁以下（如"35岁以下""30岁以下"等），排除该职位。
7. 排除党员要求：如果招聘要求中明确要求必须是党员（如"仅限党员""须为党员""党员优先"等），排除该职位。
8. 排除博士学历：如果招聘要求中明确要求博士学历（如"博士""博士研究生""PhD"等），排除该职位。硕士及以下保留。
9. 排除不相关行业：互联网金融、游戏/电竞、智能驾驶/自动驾驶/车联网、证券/期货/基金/券商/投行、广告/传媒/公关/新媒体、法律/财务/审计/税务/律师事务所/会计师事务所、房地产/物业/中介、保险/担保、教育培训/教培、物流/快递、餐饮/酒店/旅游、零售/百货/商超、建筑/施工/装修、农业/养殖等行业的职位一律排除。
10. 【重要】同一个企业如果有多个符合条件的职位，必须拆分为多个独立条目返回，每个职位一条记录，每个记录有不同的position_title。不要合并多个职位到一个记录中。

【职位数据】
{jobs_text}

请以JSON数组返回符合条件的职位，每个职位一条记录。idx字段必须填写该职位对应的原始数据序号：
[{{"idx":1,"company":"公司名","company_type":"国企/央企/外资/合资/研究所/待确认/其他","location":"地点","position_title":"职位名称","education":"学历要求","major":"专业要求（完整描述，含是否限专业）","experience":"经验要求","responsibilities":"岗位职责描述（多条用换行分隔）","requirements":"任职要求描述（多条用换行分隔，不要包含专业要求）","salary":"薪资范围","hr_activity":"HR活跃情况（如'刚刚活跃''今日活跃''3天内活跃''本周活跃'等，如无则填'未知'）","source_url":"招聘职位实际页面链接","source_site":"来源网站"}}]

【重要规则】
- idx必须是整数，对应上方数据中的序号[1]到[{len(batch)}]，必须填写
- source_url必须照抄原始数据中的链接，不要修改、缩写或编造
- 同一公司多个职位必须分开返回，每个职位一条JSON对象，idx对应原始数据序号
- 如果原始数据中某条记录包含多个职位，拆分时所有拆分记录使用同一个idx
- responsibilities中多条职责用换行符\\n分隔
- requirements中多条任职要求用换行符\\n分隔，不要包含专业相关要求
- 如果没有符合条件的返回[]
- 只返回JSON数组，不要其他文字"""

        try:
            resp = requests.post(ZHIPU_API_URL, headers={
                "Authorization": f"Bearer {ZHIPU_API_KEY}", "Content-Type": "application/json"
            }, json={
                "model": "glm-4-flash",
                "messages": [{"role": "user", "content": prompt}],
                "stream": False, "temperature": 0.1,
            }, timeout=120)
            resp.raise_for_status()
            content = resp.json().get('choices', [{}])[0].get('message', {}).get('content', '')
            batch_filtered = parse_json_from_text(content)

            # ====== 多级匹配恢复真实链接 ======
            matched_count = 0
            unmatched_count = 0
            for j in batch_filtered:
                original = None
                matched_method = ''

                # 策略1：idx匹配（主匹配）
                idx_val = j.get('idx', '')
                try:
                    if isinstance(idx_val, str):
                        idx_int = int(idx_val.strip())
                    else:
                        idx_int = int(idx_val)
                    if 1 <= idx_int <= len(batch):
                        original = batch[idx_int - 1]
                        matched_method = 'idx'
                except (ValueError, TypeError):
                    pass

                # 策略2：公司名+职位标题匹配（回退）
                if not original:
                    comp = (j.get('company', '') or '').strip().lower()
                    ptitle = (j.get('position_title', '') or '').strip().lower()
                    if comp and ptitle:
                        # 精确匹配
                        if (comp, ptitle) in original_by_comp_title:
                            original = original_by_comp_title[(comp, ptitle)]
                            matched_method = 'comp_title_exact'
                        else:
                            # 模糊匹配：公司名包含或被包含
                            for (oc, ot), ojob in original_by_comp_title.items():
                                if (comp in oc or oc in comp) and (ptitle in ot or ot in ptitle):
                                    original = ojob
                                    matched_method = 'comp_title_fuzzy'
                                    break

                # 策略3：验证LLM返回的URL是否在原始链接集合中
                if not original:
                    llm_url = j.get('source_url', '')
                    if llm_url and llm_url in original_urls:
                        # 找到对应的原始记录
                        for idx, job in enumerate(batch, 1):
                            if job.get('link') == llm_url:
                                original = job
                                matched_method = 'url_match'
                                break

                # 应用匹配结果
                if original:
                    matched_count += 1
                    # 强制使用原始链接（规范化后）
                    orig_link = _normalize_url(original.get('link', ''))
                    if orig_link and orig_link.startswith('http'):
                        j['source_url'] = orig_link
                    else:
                        j['source_url'] = ''  # 原始链接无效，置空
                    if original.get('source'):
                        j['source_site'] = original.get('source', '')
                    # 保留原始HR活跃信息
                    if original.get('hr_activity') and (not j.get('hr_activity') or j.get('hr_activity') == '未知'):
                        j['hr_activity'] = original['hr_activity']
                else:
                    unmatched_count += 1
                    # 无法匹配原始数据，标记source_url为空（后续会被过滤）
                    j['source_url'] = ''
                    comp_name = j.get('company', '未知')
                    pos_name = j.get('position_title', '未知')
                    print(f"    [WARN] 无法匹配原始数据: {comp_name} - {pos_name}")

            # ====== 二次过滤 ======
            # 薪资(按类型)+互联网大厂+年龄+党员+博士+行业+HR活跃+有效链接
            before_count = len(batch_filtered)
            batch_filtered = [j for j in batch_filtered
                if passes_salary_filter(j.get('salary', ''), j.get('company_type', ''))
                and not _is_internet_giant(j.get('company', ''))
                and not _has_age_limit(j.get('major', '') + ' ' + j.get('responsibilities', '') + ' ' + j.get('experience', ''))
                and not _has_party_requirement(j.get('major', '') + ' ' + j.get('responsibilities', ''))
                and not _requires_phd(j.get('education', '') + ' ' + j.get('major', '') + ' ' + j.get('responsibilities', ''))
                and not _is_excluded_industry(j.get('company', '') + ' ' + j.get('position_title', '') + ' ' + j.get('responsibilities', ''))
                and not _is_hr_inactive(j.get('hr_activity', ''))
                and _has_valid_link(j)]  # 必须有有效链接
            filtered_out = before_count - len(batch_filtered)
            print(f"    匹配: {matched_count} 成功, {unmatched_count} 失败 | 过滤后: {len(batch_filtered)} 个 (移除{filtered_out}个无效)")
            all_filtered.extend(batch_filtered)
        except Exception as e:
            print(f"    [ERROR] {e}")

    # 最终去重（同公司+同职位标题只保留一条，不同职位标题各自独立保留）
    seen = set()
    deduped = []
    for job in all_filtered:
        company = (job.get('company', '') or '').strip()
        position = (job.get('position_title', '') or '').strip()
        key = f"{company}_{position}"
        if key not in seen and company and position:
            seen.add(key)
            deduped.append(job)

    print(f"  去重后: {len(deduped)} 个岗位")
    return deduped


def _is_internet_giant(company_name):
    """检查公司是否为互联网大厂"""
    if not company_name:
        return False
    name_lower = company_name.lower()
    for giant in INTERNET_GIANTS:
        if giant.lower() in name_lower:
            return True
    return False


def _has_valid_link(job):
    """检查职位是否有有效的详情链接"""
    url = job.get('source_url', '') or job.get('link', '')
    if not url:
        return False
    url = _normalize_url(url)
    if not url.startswith('http'):
        return False
    # 排除明显的无效链接
    invalid_patterns = ['javascript:', '#', 'void(0)', 'about:blank']
    for p in invalid_patterns:
        if p in url:
            return False
    return True


def _normalize_url(url):
    """将URL规范化为绝对http(s) URL"""
    if not url:
        return ''
    url = url.strip()
    # 处理协议相对URL（以//开头）
    if url.startswith('//'):
        return 'https:' + url
    # 已经是绝对URL
    if url.startswith('http://') or url.startswith('https://'):
        return url
    # 其他情况（相对路径等）原样返回，后续验证会过滤
    return url


def _verify_job_link(url):
    """发HTTP请求验证链接是否指向真实职位页面。
    返回 (is_valid, reason) 元组。
    """
    if not url or not url.startswith('http'):
        return False, '无效URL'

    try:
        resp = requests.get(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            },
            timeout=8,
            allow_redirects=True,
        )

        # HTTP状态码检查
        if resp.status_code == 404:
            return False, '404页面不存在'
        if resp.status_code >= 400:
            return False, f'HTTP {resp.status_code}'

        # 检查是否被重定向到首页/登录页
        final_url = resp.url.lower()
        original_path = urllib.parse.urlparse(url).path.lower()
        final_path = urllib.parse.urlparse(final_url).path.lower()

        # 如果最终URL的路径比原始URL短很多，可能是重定向到首页
        # 例如 /job/12345.html → / 或 /login
        if final_path in ('', '/', '/index.html', '/index.htm', '/login', '/signin'):
            return False, f'重定向到首页/登录页: {final_url}'

        # 检查页面内容是否包含职位相关关键词
        content = resp.text[:5000].lower() if resp.text else ''
        job_keywords = ['职位', '招聘', '岗位', '职责', '任职', '要求', '薪资',
                        'job', 'position', 'responsibility', 'requirement', 'salary',
                        '工程师', '专员', '经理', '分析师', '研究员', '设计师']
        keyword_hits = sum(1 for kw in job_keywords if kw in content)

        if keyword_hits >= 2:
            return True, f'验证通过({keyword_hits}个关键词匹配)'
        elif keyword_hits == 1:
            return True, '验证通过(弱匹配)'
        else:
            # 没有任何职位关键词，可能是错误页面
            # 但有些页面用JS渲染，内容可能为空，宽松处理
            if resp.status_code == 200 and len(resp.text) > 500:
                return True, '验证通过(HTTP 200,内容>500字符)'
            return False, '页面无职位相关内容'

    except requests.exceptions.Timeout:
        return False, '请求超时'
    except requests.exceptions.ConnectionError:
        return False, '连接失败'
    except Exception as e:
        return False, f'异常: {str(e)[:50]}'


def verify_all_links(jobs):
    """批量验证所有职位的链接，返回验证通过的职位列表。
    会打印每个链接的验证结果。
    """
    if not jobs:
        return jobs

    print(f"\n  [链接验证] 开始验证 {len(jobs)} 个链接...")
    verified = []
    failed = []

    for i, job in enumerate(jobs, 1):
        url = _normalize_url(job.get('source_url', '') or job.get('link', ''))
        if not url:
            print(f"    [{i}/{len(jobs)}] SKIP (无链接): {job.get('company','')} - {job.get('position_title','')}")
            failed.append((job, '无链接'))
            continue

        is_valid, reason = _verify_job_link(url)
        status_icon = '✓' if is_valid else '✗'
        print(f"    [{i}/{len(jobs)}] {status_icon} {reason}: {job.get('company','')} - {job.get('position_title','')}")

        if is_valid:
            job['source_url'] = url  # 确保使用规范化后的URL
            verified.append(job)
        else:
            failed.append((job, reason))

        # 小延迟，避免请求过快
        time.sleep(0.3)

    print(f"\n  [链接验证] 完成: {len(verified)} 通过, {len(failed)} 失败")
    if failed:
        print(f"  [链接验证] 失败列表:")
        for job, reason in failed:
            print(f"    - {job.get('company','')} | {job.get('position_title','')} | {reason}")

    return verified


def _has_age_limit(text):
    """检查是否包含年龄限制"""
    if not text:
        return False
    text_lower = text.lower()
    for kw in AGE_EXCLUDE_KEYWORDS:
        if kw in text_lower:
            return True
    return False


def _has_party_requirement(text):
    """检查是否包含党员要求"""
    if not text:
        return False
    text_lower = text.lower()
    for kw in PARTY_EXCLUDE_KEYWORDS:
        if kw in text_lower:
            return True
    return False


def _requires_phd(text):
    """检查是否要求博士学历"""
    if not text:
        return False
    text_lower = text.lower()
    for kw in PHD_EXCLUDE_KEYWORDS:
        if kw.lower() in text_lower:
            return True
    return False


def _is_excluded_industry(text):
    """检查是否属于排除行业"""
    if not text:
        return False
    text_lower = text.lower()
    for kw in EXCLUDED_INDUSTRIES:
        if kw.lower() in text_lower:
            return True
    return False


def _is_hr_inactive(hr_activity):
    """检查HR是否7天内不活跃，不活跃则过滤掉"""
    if not hr_activity or hr_activity == '未知':
        return False  # 未知活跃状态不过滤，保留
    text = hr_activity.strip()
    # 活跃的关键词 - 这些都算7天内活跃
    active_keywords = ['刚刚', '今日', '今天', '在线', '活跃', '刚活跃', '刚刚活跃',
                       '1天', '2天', '3天', '4天', '5天', '6天',
                       '1日内', '2日内', '3日内',
                       '本周', '本周活跃', '本周内']
    for kw in active_keywords:
        if kw in text:
            return False
    # 如果包含"月"或"年前"等字样，说明超过7天
    if '月' in text or '年' in text:
        return True
    # 如果包含数字+天且大于7
    import re
    m = re.search(r'(\d+)\s*天', text)
    if m and int(m.group(1)) > 7:
        return True
    # 其他情况不过滤
    return False


def parse_json_from_text(text):
    if not text:
        return []
    text = text.replace('```json', '').replace('```', '').strip()
    start = text.find('[')
    end = text.rfind(']') + 1
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass
    return []


# ============ HTML 报告生成 ============

def generate_html(jobs, date_display):
    now_str = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')

    if not jobs:
        return _generate_empty_html(date_display, now_str)

    # 按公司类型排序
    jobs_sorted = sorted(jobs, key=lambda x: (
        get_type_priority(x.get('company_type', '')),
        x.get('company', ''),
        x.get('position_title', '')
    ))

    # 统计各类型数量
    type_counts = {}
    for job in jobs_sorted:
        ctype = _normalize_type(job.get('company_type', ''))
        type_counts[ctype] = type_counts.get(ctype, 0) + 1

    # 构建统计卡片
    stat_items = [
        f'<div class="stat-card"><div class="stat-num">{len(jobs_sorted)}</div><div class="stat-label">符合条件</div></div>'
    ]
    type_order = ['国企', '央企', '外资', '合资', '研究所', '待确认', '其他']
    for t in type_order:
        if type_counts.get(t, 0) > 0:
            stat_items.append(
                f'<div class="stat-card"><div class="stat-num">{type_counts[t]}</div><div class="stat-label">{t}</div></div>'
            )
    other_count = sum(v for k, v in type_counts.items() if k not in type_order)
    if other_count > 0:
        stat_items.append(
            f'<div class="stat-card"><div class="stat-num">{other_count}</div><div class="stat-label">其他</div></div>'
        )

    # 按类型分组构建卡片
    sections_html = ""
    current_type = None
    for i, job in enumerate(jobs_sorted, 1):
        ctype = _normalize_type(job.get('company_type', ''))
        if ctype != current_type:
            if current_type is not None:
                sections_html += '  </div>\n</section>\n'
            current_type = ctype
            type_class = _get_type_class(ctype)
            type_icon = _get_type_icon(ctype)
            sections_html += f'<section class="type-group">\n'
            sections_html += f'  <div class="group-header {type_class}" onclick="this.parentElement.querySelector(\'.card-grid\').style.display=this.parentElement.querySelector(\'.card-grid\').style.display==\'none\'?\'grid\':\'none\';this.classList.toggle(\'collapsed\')"><span>{type_icon} {ctype}</span><span class="group-count">{type_counts.get(ctype, 0)} 个岗位</span></div>\n'
            sections_html += f'  <div class="card-grid">\n'
        sections_html += _build_job_card(i, job)
        is_last = (i == len(jobs_sorted))
        if is_last:
            sections_html += '  </div>\n</section>\n'

    css = _get_css()
    html = f"""<!-- Generated by Trae Work -->
<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>成都招聘日报 - {date_display}</title>
<style>{css}</style>
</head><body>
<div class="container">
  <header class="header">
    <div class="header-content">
      <div class="header-title">成都招聘日报</div>
      <div class="header-sub">气象 / 环境 / 大模型(LLM) / AI Agent</div>
      <div class="header-meta">
        <span class="meta-chip">📅 {date_display}</span>
        <span class="meta-chip">📍 成都</span>
        <span class="meta-chip">💼 {len(jobs_sorted)} 个岗位</span>
        <span class="meta-chip">🔄 5轮搜索合并</span>
      </div>
    </div>
  </header>

  <section class="stat-row">{''.join(stat_items)}</section>

  <main class="job-list">
{sections_html}
  </main>

  <footer class="footer">
    <div class="footer-sources">
      <span class="footer-badge">51job</span>
      <span class="footer-badge">猎聘</span>
      <span class="footer-badge">智联招聘</span>
      <span class="footer-badge">Boss直聘</span>
      <span class="footer-badge">58同城</span>
      <span class="footer-badge">鱼泡网</span>
      <span class="footer-badge">成信大就业网</span>
      <span class="footer-badge">企业官网</span>
    </div>
    <div>5轮搜索合并(2:00 / 3:00 / 4:00 / 5:00 / 6:00) | 9:00推送 | Playwright + GLM-4-Flash | {now_str}</div>
  </footer>
</div>
<script>
document.querySelectorAll('.group-header').forEach(function(h){{
  h.addEventListener('click',function(){{
    var g=this.parentElement;
    var grid=g.querySelector('.card-grid');
    if(grid.style.display==='none'){{
      grid.style.display='grid';
      this.classList.remove('collapsed');
    }}else{{
      grid.style.display='none';
      this.classList.add('collapsed');
    }}
  }});
}});
</script>
</body></html>"""
    return html


def _normalize_type(type_str):
    if not type_str:
        return '待确认'
    if '国企' in type_str:
        return '国企'
    if '央企' in type_str:
        return '央企'
    if '外资' in type_str or '外企' in type_str:
        return '外资'
    if '合资' in type_str:
        return '合资'
    if '研究' in type_str or '事业' in type_str or '学校' in type_str or '大学' in type_str or '学院' in type_str or '院校' in type_str:
        return '研究所'
    if '其他' in type_str or '民营' in type_str or '私营' in type_str or '股份制' in type_str or '民企' in type_str:
        return '其他'
    return type_str


def _get_type_class(type_str):
    t = _normalize_type(type_str)
    mapping = {
        '国企': 't-guoqi',
        '央企': 't-yangqi',
        '外资': 't-waizi',
        '合资': 't-hezi',
        '研究所': 't-research',
    }
    return mapping.get(t, 't-other')


def _get_type_icon(type_str):
    t = _normalize_type(type_str)
    mapping = {
        '国企': '🏢',
        '央企': '🏛️',
        '外资': '🌍',
        '合资': '🤝',
        '研究所': '🔬',
    }
    return mapping.get(t, '📋')


def _get_source_class(source):
    s = (source or '').lower()
    if '猎聘' in s or 'liepin' in s: return 'src-liepin'
    if '智联' in s or 'zhaopin' in s: return 'src-zhaopin'
    if 'boss' in s or '直聘' in s: return 'src-boss'
    if '51job' in s or '前程' in s: return 'src-51job'
    if '成信' in s or 'cuit' in s: return 'src-school'
    if '官网' in s or '企业' in s: return 'src-official'
    if '58' in s: return 'src-58'
    if '鱼泡' in s or 'yupao' in s: return 'src-yupao'
    return 'src-other'


def _escape_html(text):
    if not text:
        return ''
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def _build_job_card(idx, job):
    company = _escape_html(job.get('company', '未知'))
    company_type = _escape_html(job.get('company_type', '待确认'))
    location = _escape_html(job.get('location', '成都'))
    position_title = _escape_html(job.get('position_title', job.get('title', '')))
    education = _escape_html(job.get('education', ''))
    major = _escape_html(job.get('major', ''))
    experience = _escape_html(job.get('experience', ''))
    responsibilities = job.get('responsibilities', '')
    requirements = job.get('requirements', '')
    salary = _escape_html(job.get('salary', '面议'))
    source_url = _normalize_url(job.get('source_url', '') or job.get('link', ''))
    source_site = _escape_html(job.get('source_site', job.get('source', '')))
    hr_activity = _escape_html(job.get('hr_activity', ''))

    type_class = _get_type_class(company_type)
    src_class = _get_source_class(source_site)

    # 公司名称（纯文本，不跳转）
    company_html = company

    # 职位标题
    title_html = position_title or '详见链接'

    # HR活跃情况
    hr_html = ''
    if hr_activity and hr_activity != '未知':
        hr_html = f'<span class="hr-active">{hr_activity}</span>'
    else:
        hr_html = '<span class="hr-unknown">HR活跃未知</span>'

    # 岗位职责
    resp_html = ''
    if responsibilities:
        lines = [l.strip() for l in responsibilities.split('\n') if l.strip()]
        if lines:
            items = ''.join(f'<li>{_escape_html(l)}</li>' for l in lines)
            resp_html = f'<div class="section-label">岗位职责</div><ul class="resp-list">{items}</ul>'

    # 任职要求（不含专业）
    req_html = ''
    if requirements:
        lines = [l.strip() for l in requirements.split('\n') if l.strip()]
        if lines:
            items = ''.join(f'<li>{_escape_html(l)}</li>' for l in lines)
            req_html = f'<div class="section-label">任职要求</div><ul class="resp-list">{items}</ul>'

    # 来源链接（跳转到招聘实际页面）
    source_link = ''
    if source_url and source_url.startswith('http'):
        source_link = f'<a href="{source_url}" target="_blank" rel="noopener" class="detail-link">查看详情</a>'

    # 信息行：地点 | 学历 | 经验
    info_items = [f'<span class="info-item">📍 {location}</span>']
    if education:
        info_items.append(f'<span class="info-sep">|</span><span class="info-item">🎓 {education}</span>')
    if experience:
        info_items.append(f'<span class="info-sep">|</span><span class="info-item">⏱ {experience}</span>')
    info_html = ''.join(info_items)

    return f"""    <div class="job-card">
      <div class="card-header-row">
        <span class="type-badge {type_class}">{company_type}</span>
        {hr_html}
        <span class="salary-tag">{salary}</span>
      </div>
      <div class="company-name">{company_html}</div>
      <div class="position-title">{title_html}</div>
      <div class="info-line">{info_html}</div>
      {resp_html}
      {req_html}
      <div class="card-footer-row">
        <span class="source-badge {src_class}">{source_site or '未知来源'}</span>
        {source_link}
      </div>
    </div>
"""


def _generate_empty_html(date_display, now_str):
    css = _get_css()
    return f"""<!-- Generated by Trae Work -->
<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>成都招聘日报 - {date_display}</title>
<style>{css}</style>
</head><body>
<div class="container">
  <header class="header">
    <div class="header-content">
      <div class="header-title">成都招聘日报</div>
      <div class="header-sub">气象 / 环境 / 大模型(LLM) / AI Agent</div>
      <div class="header-meta">
        <span class="meta-chip">📅 {date_display}</span>
        <span class="meta-chip">📍 成都</span>
        <span class="meta-chip">💼 0 个岗位</span>
      </div>
    </div>
  </header>
  <section class="stat-row"><div class="stat-card"><div class="stat-num">0</div><div class="stat-label">符合条件</div></div></section>
  <div class="empty-state">
    <div class="empty-icon">📭</div>
    今日未发现符合条件的岗位
  </div>
  <footer class="footer">
    <div>5轮搜索合并(2:00 / 3:00 / 4:00 / 5:00 / 6:00) | 9:00推送 | Playwright + GLM-4-Flash | {now_str}</div>
  </footer>
</div>
</body></html>"""


def _get_css():
    return """
:root{
  --bg:#f0f2f5;
  --bg2:#ffffff;
  --bg3:#f8fafc;
  --ink:#1e293b;
  --muted:#64748b;
  --muted2:#94a3b8;
  --rule:#e2e8f0;
  --accent:#4f46e5;
  --accent-light:#eef2ff;
  --accent2:#0d9488;
  --salary:#e11d48;
  --salary-bg:#fef2f2;
  --radius:12px;
  --shadow:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);
  --shadow-md:0 4px 6px rgba(0,0,0,.04),0 2px 4px rgba(0,0,0,.03);
  --shadow-lg:0 10px 25px rgba(0,0,0,.08),0 4px 10px rgba(0,0,0,.04);
  --transition:all .25s cubic-bezier(.4,0,.2,1);
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Noto Sans CJK SC','Microsoft YaHei','Segoe UI',sans-serif;background:var(--bg);color:var(--ink);line-height:1.6;font-size:14px;-webkit-font-smoothing:antialiased}
.container{max-width:1000px;margin:0 auto;padding:20px}

/* Header */
.header{background:linear-gradient(135deg,#4f46e5 0%,#7c3aed 50%,#6366f1 100%);border-radius:var(--radius);padding:32px 36px;color:#fff;margin-bottom:20px;box-shadow:0 8px 24px rgba(79,70,229,.2);position:relative;overflow:hidden}
.header::before{content:'';position:absolute;top:-50%;right:-20%;width:300px;height:300px;background:rgba(255,255,255,.06);border-radius:50%}
.header::after{content:'';position:absolute;bottom:-40%;left:-10%;width:200px;height:200px;background:rgba(255,255,255,.04);border-radius:50%}
.header-content{position:relative;z-index:1}
.header-title{font-size:26px;font-weight:800;letter-spacing:.5px;margin-bottom:4px}
.header-sub{font-size:13px;opacity:.8;margin-bottom:14px;letter-spacing:1px}
.header-meta{display:flex;gap:8px;flex-wrap:wrap}
.meta-chip{background:rgba(255,255,255,.18);backdrop-filter:blur(10px);padding:4px 14px;border-radius:20px;font-size:12px;font-weight:500;border:1px solid rgba(255,255,255,.15)}

/* Stat row */
.stat-row{display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap}
.stat-card{flex:1;min-width:100px;background:var(--bg2);border-radius:var(--radius);padding:20px 14px;text-align:center;box-shadow:var(--shadow-md);transition:var(--transition);border-bottom:3px solid var(--accent)}
.stat-card:hover{transform:translateY(-2px);box-shadow:var(--shadow-lg)}
.stat-num{font-size:32px;font-weight:800;color:var(--accent);line-height:1.1}
.stat-label{font-size:12px;color:var(--muted);margin-top:6px;font-weight:500}

/* Type groups */
.type-group{margin-bottom:22px}
.group-header{display:flex;align-items:center;justify-content:space-between;padding:10px 18px;border-radius:var(--radius) var(--radius) 0 0;font-size:15px;font-weight:700;letter-spacing:.5px;cursor:pointer;user-select:none;transition:var(--transition);position:relative}
.group-header:hover{filter:brightness(.96)}
.group-header::after{content:'▼';font-size:10px;opacity:.6;transition:transform .25s;margin-left:8px}
.group-header.collapsed::after{transform:rotate(-90deg)}
.group-count{background:rgba(255,255,255,.3);font-size:12px;padding:2px 10px;border-radius:12px;font-weight:600}
.t-guoqi{background:linear-gradient(135deg,#dcfce7,#bbf7d0);color:#166534}
.t-yangqi{background:linear-gradient(135deg,#dbeafe,#bfdbfe);color:#1e40af}
.t-waizi{background:linear-gradient(135deg,#f3e8ff,#e9d5ff);color:#6b21a8}
.t-hezi{background:linear-gradient(135deg,#fed7aa,#fdba74);color:#9a3412}
.t-research{background:linear-gradient(135deg,#cffafe,#a5f3fc);color:#155e75}
.t-other{background:linear-gradient(135deg,#f1f5f9,#e2e8f0);color:#475569}

/* Card grid */
.card-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:14px;padding:14px 0}

/* Job card */
.job-card{background:var(--bg2);border-radius:var(--radius);padding:18px 20px;box-shadow:var(--shadow);border:1px solid var(--rule);transition:var(--transition);position:relative;overflow:hidden}
.job-card::before{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--accent);opacity:0;transition:var(--transition)}
.job-card:hover{box-shadow:var(--shadow-lg);transform:translateY(-3px);border-color:var(--accent-light)}
.job-card:hover::before{opacity:1}

.card-header-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;gap:6px}
.type-badge{font-size:11px;font-weight:700;padding:3px 12px;border-radius:6px;white-space:nowrap;letter-spacing:.5px}
.type-badge.t-guoqi{background:#dcfce7;color:#166534}
.type-badge.t-yangqi{background:#dbeafe;color:#1e40af}
.type-badge.t-waizi{background:#f3e8ff;color:#6b21a8}
.type-badge.t-hezi{background:#fed7aa;color:#9a3412}
.type-badge.t-research{background:#cffafe;color:#155e75}
.type-badge.t-other{background:#f1f5f9;color:#475569}

.hr-active{font-size:11px;font-weight:600;padding:2px 8px;border-radius:5px;background:#dcfce7;color:#166534;white-space:nowrap}
.hr-unknown{font-size:11px;font-weight:600;padding:2px 8px;border-radius:5px;background:#f1f5f9;color:#94a3b8;white-space:nowrap}

.salary-tag{font-size:17px;font-weight:800;color:var(--salary);white-space:nowrap;background:var(--salary-bg);padding:2px 10px;border-radius:6px;margin-left:auto}

.company-name{font-size:16px;font-weight:700;margin-bottom:3px}
.company-name a{color:var(--ink);text-decoration:none;transition:var(--transition)}
.company-name a:hover{color:var(--accent)}

.position-title{font-size:14px;font-weight:600;color:var(--accent);margin-bottom:10px;display:flex;align-items:center;gap:6px}
.position-title::before{content:'';width:3px;height:14px;background:var(--accent);border-radius:2px;flex-shrink:0}

.info-line{font-size:13px;color:var(--muted);margin-bottom:8px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.info-item{display:inline-flex;align-items:center;gap:3px}
.info-sep{color:var(--rule);margin:0 2px}

.section-label{font-size:12px;font-weight:700;color:var(--accent2);margin:8px 0 4px 0;padding:2px 0;border-left:3px solid var(--accent2);padding-left:8px}

.resp-list{margin:4px 0 8px 0;font-size:13px;color:var(--muted);padding-left:0;list-style:none}
.resp-list li{margin-bottom:3px;padding-left:16px;position:relative;line-height:1.5}
.resp-list li::before{content:'\\25B8';position:absolute;left:0;color:var(--accent2);font-size:11px}

.card-footer-row{display:flex;justify-content:space-between;align-items:center;margin-top:12px;padding-top:10px;border-top:1px solid var(--rule)}
.source-badge{font-size:11px;padding:3px 10px;border-radius:5px;font-weight:600;display:inline-flex;align-items:center;gap:4px}
.source-badge::before{content:'\\25C9';font-size:8px}
.src-liepin{background:#dcfce7;color:#166534}
.src-zhaopin{background:#dbeafe;color:#1e40af}
.src-boss{background:#fed7aa;color:#9a3412}
.src-51job{background:#fce7f3;color:#9d174d}
.src-school{background:#f3e8ff;color:#6b21a8}
.src-official{background:#cffafe;color:#155e75}
.src-58{background:#fef9c3;color:#854d0e}
.src-yupao{background:#e0e7ff;color:#3730a3}
.src-other{background:#f1f5f9;color:#475569}
.detail-link{font-size:12px;color:var(--accent);text-decoration:none;font-weight:600;transition:var(--transition);display:inline-flex;align-items:center;gap:3px}
.detail-link:hover{text-decoration:underline;gap:5px}
.detail-link::after{content:'\\2192';transition:var(--transition)}

/* Empty */
.empty-state{background:var(--bg2);border-radius:var(--radius);padding:70px 20px;text-align:center;color:var(--muted);font-size:18px;margin-bottom:20px;box-shadow:var(--shadow)}
.empty-state .empty-icon{font-size:48px;margin-bottom:16px;opacity:.3}

/* Footer */
.footer{text-align:center;padding:24px 10px;color:var(--muted2);font-size:11px;line-height:1.9}
.footer-sources{margin-bottom:6px;color:var(--muted)}
.footer-badge{display:inline-block;background:var(--bg2);padding:3px 10px;border-radius:4px;margin:0 2px;box-shadow:var(--shadow);font-size:10px;color:var(--muted)}

/* Mobile */
@media(max-width:640px){
  .container{padding:12px}
  .header{padding:24px 20px}
  .header-title{font-size:21px}
  .card-grid{grid-template-columns:1fr}
  .stat-card{min-width:70px;padding:14px 8px}
  .stat-num{font-size:26px}
  .filter-row{padding:12px 16px}
}
"""


# ============ Server酱 ============

def send_notification(title, desp):
    if not SERVERCHAN_KEY:
        print("[WARN] SERVERCHAN_KEY 未设置")
        return
    try:
        url = f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"
        resp = requests.post(url, data={"title": title, "desp": desp}, timeout=30)
        if resp.status_code == 200 and resp.json().get('code') == 0:
            print(f"[OK] 微信通知已发送: {title}")
        else:
            print(f"[ERROR] 通知失败: {resp.text[:200]}")
    except Exception as e:
        print(f"[ERROR] {e}")


# ============ 轮次检测 ============

def get_search_round():
    """自动检测当前搜索轮次：1-5为搜索轮，6为合并推送轮"""
    round_env = os.environ.get('SEARCH_ROUND', '0')
    if round_env and round_env != '0':
        return int(round_env)
    # 根据北京时间自动判断
    tz = timezone(timedelta(hours=8))
    hour = datetime.now(tz).hour
    if 2 <= hour < 3:
        return 1
    elif 3 <= hour < 4:
        return 2
    elif 4 <= hour < 5:
        return 3
    elif 5 <= hour < 6:
        return 4
    elif 6 <= hour < 7:
        return 5
    elif hour >= 9:
        return 6  # 合并推送轮
    else:
        return 5  # 默认最后一轮搜索


# ============ 主流程 ============

def main():
    if not ZHIPU_API_KEY:
        print("[ERROR] ZHIPU_API_KEY 未设置")
        sys.exit(1)
    if not SERVERCHAN_KEY:
        print("[ERROR] SERVERCHAN_KEY 未设置")
        sys.exit(1)

    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    date_str = now.strftime('%Y%m%d')
    date_display = now.strftime('%Y-%m-%d')
    round_num = get_search_round()

    print(f"{'='*60}")
    print(f"成都招聘日报 v6 {date_display} (轮次{round_num}/6)")
    print(f"平台: 51job/猎聘/智联招聘/Boss直聘/58同城/鱼泡网/成信大就业网/企业官网")
    print(f"薪资门槛: 国企/央企/外资/合资/研究所≥{MIN_SALARY}元/月 | 其他≥{OTHER_MIN_SALARY}元/月")
    print(f"公司类型: 国企/央企/外资/合资/研究所(含学校)/其他 | 排除互联网大厂/年龄35岁以下/仅限党员/博士")
    print(f"{'='*60}")

    os.makedirs(REPORT_DIR, exist_ok=True)
    os.makedirs(RAW_DIR, exist_ok=True)

    # 轮次1-5：仅搜索并保存中间结果
    if round_num <= 5:
        print(f"\n[轮次{round_num}] 搜索招聘信息...")
        round_jobs = scrape_round(round_num)
        save_round_results(round_num, round_jobs, date_str)
        print(f"\n轮次{round_num}完成，中间结果已保存。等待后续轮次...")
        print(f"{'='*60}")
        return

    # 轮次6：合并所有轮次结果 + LLM筛选 + 生成HTML + 推送
    print(f"\n[轮次6] 合并所有5轮搜索结果...")
    all_raw_jobs = load_all_round_results(date_str, 5)
    if not all_raw_jobs:
        print("[WARN] 未抓取到任何职位")

    # LLM筛选
    print(f"\n[轮次6] GLM-4-Flash 智能筛选...")
    filtered = filter_jobs_with_llm(all_raw_jobs)
    print(f"  筛选后: {len(filtered)} 个岗位")

    # 链接验证：对每个链接发HTTP请求，验证是否指向真实职位页面
    if filtered:
        print(f"\n[轮次6] 链接验证（HTTP请求检查每个链接）...")
        filtered = verify_all_links(filtered)
        print(f"  链接验证后: {len(filtered)} 个岗位")

    # 生成HTML报告
    print(f"\n[轮次6] 生成 HTML 报告...")
    filename = f"{date_str}.html" if filtered else "无.html"
    filepath = os.path.join(REPORT_DIR, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(generate_html(filtered, date_display))
    print(f"  报告: {filepath}")

    # 推送微信通知
    print(f"\n[轮次6] 推送微信通知...")
    # GitHub Pages 报告链接
    pages_url = f"https://oniking099.github.io/chengdu-job-tracker/reports/{filename}"
    if filtered:
        title = f"成都招聘日报 {date_display}（{len(filtered)}个岗位）"
        lines = [f"今日共找到 **{len(filtered)}** 个符合条件岗位（全职社招 | 月薪≥1.2万/其他≥1.8万）\n"]
        lines.append(f"\n**[点击查看完整招聘报告]({pages_url})**\n")
        # 按类型分组汇总
        type_groups = {}
        for job in filtered:
            ctype = _normalize_type(job.get('company_type', ''))
            type_groups.setdefault(ctype, []).append(job)
        for ctype in ['国企', '央企', '外资', '合资', '研究所', '待确认', '其他']:
            if ctype not in type_groups:
                continue
            lines.append(f"\n### {ctype}（{len(type_groups[ctype])}个）\n")
            for job in type_groups[ctype]:
                company = job.get('company', '未知')
                pos = job.get('position_title', job.get('title', ''))
                salary = job.get('salary', '')
                src = job.get('source_site', job.get('source', ''))
                url = job.get('source_url', '')
                comp_str = f"[{company}]({url})" if url and url.startswith('http') else f"**{company}**"
                lines.append(f"- {comp_str} | {pos} | {salary} | {src}\n")
        lines.append(f"\n---\n完整报告（卡片UI）: [{pages_url}]({pages_url})")
        desp = '\n'.join(lines)
    else:
        title = f"成都招聘日报 {date_display} - 无符合条件岗位"
        desp = f"5轮搜索共抓取 {len(all_raw_jobs)} 个职位，筛选后无符合条件岗位。\n\n报告: {pages_url}"
    send_notification(title, desp)

    # 清理中间结果
    print(f"\n[轮次6] 清理中间结果...")
    cleanup_raw_files(date_str)

    print(f"\n{'='*60}")
    print("完成! 5轮搜索已合并并推送。")


if __name__ == '__main__':
    main()
