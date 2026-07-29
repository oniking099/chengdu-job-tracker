#!/usr/bin/env python3
"""
成都气象环境AI招聘信息日报 v3
改进点：
1. 全职+社招过滤（排除兼职/校招/实习）
2. 多平台抓取：51job/猎聘/Boss直聘/58同城/鱼泡网
3. 成都企业库：国企/央企/外资/合资公司官网招聘页抓取
4. 51job: 从 window.__SEARCH_RESULT__ 提取 JSON
5. 猎聘: API + 浏览器降级
6. 反检测: stealth 模式
7. HTML报告: 公司名可点击跳转、标注来源
"""

import os
import sys
import json
import time
import random
import requests
import urllib.parse
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

# ============ 配置 ============
ZHIPU_API_KEY = os.environ.get('ZHIPU_API_KEY', '')
SERVERCHAN_KEY = os.environ.get('SERVERCHAN_KEY', '')
REPORT_DIR = os.environ.get('REPORT_DIR', 'reports')
ZHIPU_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

# 搜索关键词
SEARCH_KEYWORDS = [
    "气象", "大气科学", "环境科学", "环境工程",
    "大模型", "LLM", "AI agent", "人工智能算法",
    "气候", "生态环境", "气象算法"
]

# Boss/58 使用精简关键词（避免被反爬）
SEARCH_KEYWORDS_LITE = ["气象", "环境科学", "大模型", "AI", "气候"]

# 51job 城市代码：090200 = 成都
JOB51_CITY = "090200"
# 猎聘城市代码：280 = 成都
LIEPIN_CITY = "280"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# 排除关键词（兼职/校招/实习）
EXCLUDE_KEYWORDS = ["校招", "校园招聘", "应届", "实习", "兼职", "寒暑假", "暑期", "寒假", "暑假"]


# ============ 成都企业库 ============
# 国企/央企/外资/合资公司 + 官网招聘页

CHENGDU_COMPANIES = [
    # === 央企/国企（精选与气象/环境/AI最相关的）===
    {"name": "中国电建集团成都勘测设计研究院", "type": "央企", "career_url": "https://www.powerchina-cdc.com/"},
    {"name": "中国核动力研究设计院", "type": "央企", "career_url": "https://www.npic.ac.cn/"},
    {"name": "东方电气集团", "type": "央企", "career_url": "https://www.dec.ltd/cn/careers"},
    {"name": "中国建筑西南设计研究院", "type": "央企", "career_url": "https://www.csweadi.com/"},
    {"name": "国家电网四川省电力公司", "type": "国企", "career_url": "https://www.sgcc.com.cn/"},
    {"name": "中国电信四川分公司", "type": "国企", "career_url": "https://www.chinatelecom.com.cn/careers/"},
    {"name": "中国移动四川分公司", "type": "国企", "career_url": "https://hr.10086.cn/"},
    {"name": "中国节能环保集团", "type": "央企", "career_url": "https://www.cecic.com.cn/"},
    {"name": "中国电科网络空间安全研究院", "type": "央企", "career_url": "https://www.cetc.com.cn/"},
    {"name": "四川长虹电器股份有限公司", "type": "国企", "career_url": "https://www.changhong.com.cn/"},

    # === 外资（精选有AI/大模型岗位的）===
    {"name": "英特尔产品(成都)", "type": "外资", "career_url": "https://www.intel.com/content/www/us/en/jobs/jobs-at-intel.html"},
    {"name": "IBM成都", "type": "外资", "career_url": "https://www.ibm.com/careers"},
    {"name": "西门子成都", "type": "外资", "career_url": "https://new.siemens.com/cn/zh/company/jobs.html"},
    {"name": "微软成都", "type": "外资", "career_url": "https://careers.microsoft.com/"},
    {"name": "亚马逊成都", "type": "外资", "career_url": "https://www.amazon.jobs/"},

    # === 合资 ===
    {"name": "一汽-大众成都", "type": "合资", "career_url": "https://www.faw-vw.com/"},
]


# ============ 浏览器配置（反检测） ============

def create_browser_context(playwright):
    """创建带反检测的浏览器上下文"""
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


def is_excluded_job(title, tags=""):
    """检查是否为兼职/校招/实习等应排除的职位"""
    text = (title + " " + tags).lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw in text:
            return True
    return False


# ============ 51job 抓取 ============

def scrape_51job(playwright, keyword):
    """前程无忧 - 从 window.__SEARCH_RESULT__ 或 DOM 提取"""
    print(f"  [51job] 搜索: {keyword}")
    jobs = []
    browser, context = create_browser_context(playwright)
    page = context.new_page()
    try:
        url = f"https://we.51job.com/pc/search?keyword={urllib.parse.quote(keyword)}&jobArea={JOB51_CITY}&sortType=0"
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        time.sleep(random.uniform(3, 5))
        for _ in range(3):
            page.mouse.wheel(0, random.randint(300, 800))
            time.sleep(0.5)

        raw_jobs = page.evaluate("""() => {
            try {
                if (window.__SEARCH_RESULT__) {
                    const result = window.__SEARCH_RESULT__;
                    const jobs = result.engine_search_result || result.joblist || [];
                    return jobs.map(j => ({
                        title: j.job_name || '', company: j.company_name || '',
                        salary: j.providesalary_text || j.salary || '',
                        location: j.workarea_text || (j.attribute_text && j.attribute_text[0]) || '成都',
                        tags: (j.attribute_text || []).join(' | ') + ' | ' + (j.jobwelf || ''),
                        link: j.job_href || '', source: '51job'
                    }));
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
                    const le = c.querySelector('a[href*="job"], .jname a, a.t1');
                    if (g('.jname') || g('.cname'))
                        jobs.push({title:g('.jname, .t1 a, [class*="jname"]'),company:g('.cname, [class*="cname"]'),
                            salary:g('.sal, [class*="sal"]'),location:g('.info .name, [class*="area"]')||'成都',
                            tags:c.textContent.trim().substring(0,300),link:le?le.href:'',source:'51job'});
                });
                return jobs;
            }""")

        seen = set()
        for job in (raw_jobs or []):
            if is_excluded_job(job.get('title', ''), job.get('tags', '')):
                continue
            key = f"{job.get('title','')}_{job.get('company','')}"
            if key not in seen and (job.get('title') or job.get('company')):
                seen.add(key)
                jobs.append(job)
        print(f"    提取到 {len(jobs)} 个职位（已排除兼职/校招）")
    except Exception as e:
        print(f"    [ERROR] {e}")
    finally:
        browser.close()
    return jobs


# ============ 猎聘抓取（API + 浏览器） ============

def scrape_liepin_api(keyword):
    """猎聘 API"""
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
                ji = card.get("job", {}); ci = card.get("comp", {})
                title = ji.get("title", "")
                tags = " | ".join(ji.get("labels", [])) + f" | {ci.get('compScale','')} | {ci.get('industryName','')}"
                if is_excluded_job(title, tags):
                    continue
                link = ji.get("link", "")
                if link and not link.startswith("http"):
                    link = "https://www.liepin.com" + link
                jobs.append({"title": title, "company": ci.get("compName", ""), "salary": ji.get("salary", ""),
                    "location": ji.get("dq", "成都"), "tags": tags, "link": link, "source": "猎聘"})
            print(f"    [API] 返回 {len(jobs)} 个职位")
        else:
            print(f"    [WARN] API {resp.status_code}")
    except Exception as e:
        print(f"    [WARN] API异常: {e}")
    return jobs


def scrape_liepin_browser(playwright, keyword):
    """猎聘浏览器降级"""
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
                const le = c.querySelector('a[href*="/job/"], a');
                if (g('.job-title') || g('[class*="title"]'))
                    jobs.push({title:g('.job-title,[class*="title"],h3'),company:g('[class*="company"],[class*="comp"]'),
                        salary:g('[class*="salary"]'),location:g('[class*="area"]')||'成都',
                        tags:c.textContent.trim().substring(0,500),link:le?le.href:'',source:'猎聘'});
            });
            return jobs;
        }""")
        for job in (raw_jobs or []):
            if not is_excluded_job(job.get('title', ''), job.get('tags', '')):
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


# ============ Boss直聘抓取 ============

def scrape_boss(playwright, keyword):
    """Boss直聘"""
    print(f"  [Boss直聘] 搜索: {keyword}")
    jobs = []
    browser, context = create_browser_context(playwright)
    page = context.new_page()
    try:
        url = f"https://www.zhipin.com/web/geek/job?query={urllib.parse.quote(keyword)}&city=101270100"
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        time.sleep(random.uniform(5, 8))
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
                        jobs.push({title:t,company:'',salary:'',location:'成都',tags:'',link:a.href,source:'Boss直聘'});
                });
                return jobs;
            }
            cards.forEach(c => {
                const g = s => { const e = c.querySelector(s); return e ? e.textContent.trim() : ''; };
                const le = c.querySelector('a[href*="/job_detail/"], a');
                if (g('.job-name') || g('[class*="job-title"]'))
                    jobs.push({title:g('.job-name,[class*="job-title"],h3'),company:g('.company-name,[class*="company"]'),
                        salary:g('.salary,[class*="salary"]'),location:g('.job-area,[class*="area"]')||'成都',
                        tags:c.textContent.trim().substring(0,500),link:le?le.href:'',source:'Boss直聘'});
            });
            return jobs;
        }""")
        for job in (raw_jobs or []):
            if not is_excluded_job(job.get('title', ''), job.get('tags', '')):
                jobs.append(job)
        print(f"    提取到 {len(jobs)} 个职位")
    except Exception as e:
        print(f"    [ERROR] {e}")
    finally:
        browser.close()
    return jobs


# ============ 58同城抓取 ============

def scrape_58(playwright, keyword):
    """58同城"""
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
                const le = c.querySelector('a');
                if (g('.job_name') || g('[class*="title"]'))
                    jobs.push({title:g('.job_name,[class*="title"],h3'),company:g('.company_name,[class*="company"]'),
                        salary:g('.salary,[class*="salary"]'),location:g('.address,[class*="area"]')||'成都',
                        tags:c.textContent.trim().substring(0,500),link:le?le.href:'',source:'58同城'});
            });
            return jobs;
        }""")
        for job in (raw_jobs or []):
            if not is_excluded_job(job.get('title', ''), job.get('tags', '')):
                jobs.append(job)
        print(f"    提取到 {len(jobs)} 个职位")
    except Exception as e:
        print(f"    [ERROR] {e}")
    finally:
        browser.close()
    return jobs


# ============ 鱼泡网抓取 ============

def scrape_yupao(playwright, keyword):
    """鱼泡网"""
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
                const le = c.querySelector('a');
                if (g('.title') || g('[class*="job-name"]'))
                    jobs.push({title:g('.title,[class*="job-name"]'),company:g('.company,[class*="company"]'),
                        salary:g('.salary,[class*="salary"]'),location:g('.area,[class*="city"]')||'成都',
                        tags:c.textContent.trim().substring(0,500),link:le?le.href:'',source:'鱼泡网'});
            });
            return jobs;
        }""")
        for job in (raw_jobs or []):
            if not is_excluded_job(job.get('title', ''), job.get('tags', '')):
                jobs.append(job)
        print(f"    提取到 {len(jobs)} 个职位")
    except Exception as e:
        print(f"    [ERROR] {e}")
    finally:
        browser.close()
    return jobs


# ============ 企业官网招聘页抓取 ============

def scrape_company_career(context, company):
    """抓取企业官网招聘页（复用浏览器上下文）"""
    name = company['name']
    ctype = company['type']
    career_url = company['career_url']
    print(f"  [企业官网] {name} ({ctype})")

    jobs = []
    page = context.new_page()
    try:
        page.goto(career_url, timeout=20000, wait_until="domcontentloaded")
        time.sleep(random.uniform(1, 2))

        # 提取页面中的招聘信息
        raw_jobs = page.evaluate("""(companyName) => {
            const jobs = [];
            const sels = ['.job-item', '.position-item', '.job-card', '[class*="job-list"] li',
                '.recruit-list li', '.job_content', '[class*="position"]', '[class*="recruit"]',
                '.list-item', '.job-list-item', '[data-job-id]'];
            let cards = [];
            for (const s of sels) { cards = document.querySelectorAll(s); if (cards.length) break; }
            if (cards.length === 0) {
                document.querySelectorAll('a').forEach(a => {
                    const text = a.textContent.trim();
                    const href = a.href;
                    if (href && text.length > 2 && text.length < 80 &&
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
                const le = c.querySelector('a');
                const title = g('.job-name, .job-title, .position-name, [class*="title"], h3, h4');
                if (title)
                    jobs.push({title:title, company:companyName, salary:g('.salary, [class*="salary"]'),
                        location:g('.area, [class*="city"]')||'成都',
                        tags:c.textContent.trim().substring(0,500), link:le?le.href:'',
                        source:'企业官网'});
            });
            return jobs;
        }""", name)

        for job in (raw_jobs or []):
            job['company_type'] = ctype
            if not is_excluded_job(job.get('title', ''), job.get('tags', '')):
                jobs.append(job)
        print(f"    提取到 {len(jobs)} 个职位")
    except Exception as e:
        print(f"    [ERROR] {e}")
    finally:
        page.close()
    return jobs


# ============ 汇总抓取 ============

def scrape_all_jobs():
    """抓取所有平台的职位"""
    all_jobs = []
    seen = set()

    def add_jobs(jobs_list):
        for j in jobs_list:
            key = f"{j.get('title','')}_{j.get('company','')}"
            if key not in seen and (j.get('title') or j.get('company')):
                seen.add(key)
                all_jobs.append(j)

    with sync_playwright() as p:
        # === 招聘平台 ===
        for i, keyword in enumerate(SEARCH_KEYWORDS, 1):
            print(f"\n[{i}/{len(SEARCH_KEYWORDS)}] 关键词: {keyword}")
            add_jobs(scrape_51job(p, keyword))
            add_jobs(scrape_liepin(p, keyword))

            # Boss/58 用精简关键词
            if keyword in SEARCH_KEYWORDS_LITE:
                add_jobs(scrape_boss(p, keyword))
                add_jobs(scrape_58(p, keyword))
                add_jobs(scrape_yupao(p, keyword))

            time.sleep(random.uniform(1, 2))

        # === 企业官网（复用同一浏览器上下文）===
        print(f"\n--- 企业官网招聘页抓取 ({len(CHENGDU_COMPANIES)}家企业) ---")
        browser2, context2 = create_browser_context(p)
        for i, company in enumerate(CHENGDU_COMPANIES, 1):
            if i % 5 == 0:
                print(f"  进度: {i}/{len(CHENGDU_COMPANIES)}")
            add_jobs(scrape_company_career(context2, company))
            time.sleep(random.uniform(0.3, 0.8))
        browser2.close()

    print(f"\n去重后总计: {len(all_jobs)} 个职位")
    return all_jobs


# ============ GLM-4-Flash 智能筛选 ============

def filter_jobs_with_llm(jobs):
    """使用 GLM-4-Flash 智能筛选"""
    if not jobs:
        return []

    BATCH_SIZE = 30
    all_filtered = []

    for batch_start in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[batch_start:batch_start + BATCH_SIZE]
        print(f"  筛选批次 {batch_start // BATCH_SIZE + 1}（{len(batch)} 个）...")

        jobs_text = ""
        for idx, job in enumerate(batch, 1):
            jobs_text += f"\n{idx}. 职位: {job.get('title', '')}\n"
            jobs_text += f"   公司: {job.get('company', '')}（类型: {job.get('company_type', '未知')}）\n"
            jobs_text += f"   薪资: {job.get('salary', '')}\n"
            jobs_text += f"   地点: {job.get('location', '')}\n"
            jobs_text += f"   标签/要求: {job.get('tags', '')[:300]}\n"
            jobs_text += f"   来源: {job.get('source', '')}\n"
            if job.get('link'):
                jobs_text += f"   链接: {job['link']}\n"

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
4. 公司类别：必须是国企/央企/外资/合资/研究所/事业单位。
   - 民营、创业公司排除。如果无法判断，标注"待确认"并保留。
5. 薪资：月薪不低于10000元。"面议"保留。低于1万/月排除。

【职位数据】
{jobs_text}

请以JSON数组返回符合条件的职位：
[{{"company":"公司名","company_type":"国企/央企/外资/合资/研究所/事业单位/待确认","location":"地点","requirements":"招聘要求（尽量完整）","salary":"薪资范围","source_url":"来源链接","source_site":"来源网站"}}]

注意：source_url必须是上面数据中对应的链接。如果没有符合条件的返回[]。只返回JSON。"""

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
            all_filtered.extend(batch_filtered)
            print(f"    本批: {len(batch_filtered)} 个")
        except Exception as e:
            print(f"    [ERROR] {e}")

    return all_filtered


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


# ============ HTML 报告 ============

def generate_html(jobs, date_display):
    now_str = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')

    if not jobs:
        return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>成都招聘日报 - {date_display}</title>
<style>
body {{ font-family:'Microsoft YaHei','Noto Sans CJK SC',sans-serif; margin:0; padding:20px; background:#f5f5f5; }}
.container {{ max-width:800px; margin:0 auto; background:white; padding:40px; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); text-align:center; }}
h1 {{ color:#333; border-bottom:2px solid #9e9e9e; padding-bottom:10px; }}
.no-results {{ color:#999; font-size:18px; padding:40px 0; }}
.footer {{ margin-top:30px; text-align:center; color:#bbb; font-size:12px; }}
</style></head><body><div class="container">
<h1>成都招聘日报 - {date_display}</h1>
<div class="no-results">今日未发现符合条件的岗位</div>
<div class="footer">由 GitHub Actions + Playwright 自动生成 | {now_str}</div>
</div></body></html>"""

    rows = []
    for i, job in enumerate(jobs, 1):
        company = job.get('company', '未知')
        company_type = job.get('company_type', '未知')
        location = job.get('location', '未知')
        requirements = job.get('requirements', '未知')
        salary = job.get('salary', '未知')
        source_url = job.get('source_url', '')
        source_site = job.get('source_site', job.get('source', ''))

        type_class = 'type-other'
        if '国企' in company_type or '央企' in company_type:
            type_class = 'type-guoqi'
        elif '外资' in company_type or '外企' in company_type:
            type_class = 'type-waizi'
        elif '合资' in company_type:
            type_class = 'type-hezi'
        elif '研究' in company_type or '事业' in company_type:
            type_class = 'type-research'

        if source_url and source_url.startswith('http'):
            company_link = f'<a href="{source_url}" target="_blank" class="company-link">{company}</a>'
        else:
            company_link = company

        source_badge = f'<span class="source-badge">{source_site}</span>' if source_site else ''

        rows.append(f"""<tr>
<td>{i}</td>
<td>{company_link}{source_badge}</td>
<td><span class="company-type {type_class}">{company_type}</span></td>
<td>{location}</td>
<td>{requirements}</td>
<td class="salary">{salary}</td>
</tr>""")

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>成都招聘日报 - {date_display}</title>
<style>
body {{ font-family:'Microsoft YaHei','Noto Sans CJK SC',sans-serif; margin:0; padding:20px; background:#f5f5f5; }}
.container {{ max-width:1200px; margin:0 auto; background:white; padding:30px; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.1); }}
h1 {{ color:#333; border-bottom:2px solid #4CAF50; padding-bottom:10px; }}
.summary {{ background:#e8f5e9; padding:15px; border-radius:4px; margin:20px 0; font-size:16px; }}
table {{ width:100%; border-collapse:collapse; margin-top:20px; }}
th,td {{ padding:12px; text-align:left; border:1px solid #ddd; font-size:14px; vertical-align:top; }}
th {{ background:#4CAF50; color:white; white-space:nowrap; }}
tr:nth-child(even) {{ background:#f9f9f9; }}
tr:hover {{ background:#f0f0f0; }}
.salary {{ color:#e65100; font-weight:bold; white-space:nowrap; }}
.company-type {{ display:inline-block; padding:2px 8px; border-radius:3px; font-size:12px; white-space:nowrap; }}
.type-guoqi {{ background:#ffe0b2; color:#e65100; }}
.type-waizi {{ background:#e3f2fd; color:#1565c0; }}
.type-hezi {{ background:#f3e5f5; color:#7b1fa2; }}
.type-research {{ background:#e0f7fa; color:#00695c; }}
.type-other {{ background:#f5f5f5; color:#666; }}
.company-link {{ color:#1976d2; text-decoration:none; font-weight:500; }}
.company-link:hover {{ text-decoration:underline; color:#0d47a1; }}
.source-badge {{ display:inline-block; margin-left:6px; padding:1px 6px; border-radius:3px; font-size:11px; background:#e8eaf6; color:#3f51b5; }}
.footer {{ margin-top:30px; text-align:center; color:#bbb; font-size:12px; }}
</style></head><body><div class="container">
<h1>成都招聘日报 - {date_display}</h1>
<div class="summary">共找到 <strong>{len(jobs)}</strong> 个符合条件岗位（全职社招 | 国企/央企/外资/合资 | 薪资≥1万 | 气象/环境/大模型/AI）<br>
<span style="font-size:13px;color:#666;">点击公司名称可跳转到原始招聘页面 | 数据来源：51job/猎聘/Boss直聘/58同城/鱼泡网/企业官网</span></div>
<table><thead><tr>
<th>#</th><th>公司名称</th><th>公司类别</th><th>公司地点</th><th>招聘要求</th><th>薪资范围</th>
</tr></thead><tbody>
{chr(10).join(rows)}
</tbody></table>
<div class="footer">由 GitHub Actions + Playwright 自动生成 | {now_str}</div>
</div></body></html>"""


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


# ============ 主流程 ============

def main():
    if not ZHIPU_API_KEY:
        print("[ERROR] ZHIPU_API_KEY 未设置"); sys.exit(1)
    if not SERVERCHAN_KEY:
        print("[ERROR] SERVERCHAN_KEY 未设置"); sys.exit(1)

    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    date_str = now.strftime('%Y%m%d')
    date_display = now.strftime('%Y-%m-%d')

    print(f"{'='*60}")
    print(f"成都招聘日报 v3 {date_display}")
    print(f"平台: 51job/猎聘/Boss直聘/58同城/鱼泡网/企业官网")
    print(f"企业库: {len(CHENGDU_COMPANIES)}家成都国企/央企/外资/合资")
    print(f"{'='*60}")

    # 1. 抓取
    print(f"\n[1/4] 抓取招聘信息...")
    raw_jobs = scrape_all_jobs()
    if not raw_jobs:
        print("[WARN] 未抓取到任何职位")

    # 2. LLM筛选
    print(f"\n[2/4] GLM-4-Flash 智能筛选...")
    filtered = filter_jobs_with_llm(raw_jobs)
    print(f"  筛选后: {len(filtered)} 个岗位")

    # 3. HTML
    print(f"\n[3/4] 生成 HTML 报告...")
    os.makedirs(REPORT_DIR, exist_ok=True)
    filename = f"{date_str}.html" if filtered else "无.html"
    filepath = os.path.join(REPORT_DIR, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(generate_html(filtered, date_display))
    print(f"  报告: {filepath}")

    # 4. 微信
    print(f"\n[4/4] 推送微信通知...")
    if filtered:
        title = f"成都招聘日报 {date_display}（{len(filtered)}个岗位）"
        lines = [f"今日共找到 **{len(filtered)}** 个符合条件岗位（全职社招）：\n"]
        for i, job in enumerate(filtered, 1):
            company = job.get('company', '未知')
            ctype = job.get('company_type', '')
            salary = job.get('salary', '')
            loc = job.get('location', '')
            src = job.get('source_site', job.get('source', ''))
            url = job.get('source_url', '')
            req = job.get('requirements', '')[:80]
            comp_str = f"[{company}]({url})" if url and url.startswith('http') else f"**{company}**"
            lines.append(f"{i}. {comp_str}（{ctype}）\n   薪资: {salary} | 地点: {loc} | 来源: {src}\n")
            if req:
                lines.append(f"   要求: {req}...\n")
        lines.append(f"\n完整报告: reports/{filename}")
        desp = '\n'.join(lines)
    else:
        title = f"成都招聘日报 {date_display} - 无符合条件岗位"
        desp = f"抓取到 {len(raw_jobs)} 个职位，筛选后无符合条件岗位。\n\n报告: reports/{filename}"
    send_notification(title, desp)

    print(f"\n{'='*60}")
    print("完成!")


if __name__ == '__main__':
    main()
