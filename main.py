#!/usr/bin/env python3
"""
成都气象环境AI招聘信息日报 v2
改进点：
1. 51job: 从 window.__SEARCH_RESULT__ 提取 JSON 数据（非DOM选择器）
2. 猎聘: 直接调用 API 接口获取结构化数据
3. 反检测: stealth 模式，禁用自动化标志
4. HTML报告: 公司名可点击跳转、标注来源
5. 微信通知: 包含来源和链接
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

# 搜索关键词 - 扩大范围
SEARCH_KEYWORDS = [
    "气象", "大气科学", "环境科学", "环境工程",
    "大模型", "LLM", "AI agent", "人工智能算法",
    "气候", "生态环境", "气象算法"
]

# 51job 城市代码：090200 = 成都
JOB51_CITY = "090200"

# 猎聘城市代码：280 = 成都
LIEPIN_CITY = "280"

# 通用 User-Agent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


# ============ 浏览器配置（反检测） ============

def create_browser_context(playwright):
    """创建带反检测的浏览器上下文"""
    browser = playwright.chromium.launch(
        headless=True,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--disable-features=IsolateOrigins,site-per-process',
            '--disable-web-security',
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
        extra_http_headers={
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    # 注入 stealth 脚本（隐藏 webdriver 标志）
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
        window.chrome = { runtime: {} };
    """)
    return browser, context


# ============ 51job 抓取（从 __SEARCH_RESULT__ 提取） ============

def scrape_51job(playwright, keyword):
    """抓取前程无忧 - 从 window.__SEARCH_RESULT__ 提取 JSON 数据"""
    print(f"  [51job] 搜索: {keyword}")
    jobs = []
    browser, context = create_browser_context(playwright)
    page = context.new_page()

    try:
        url = f"https://we.51job.com/pc/search?keyword={keyword}&jobArea={JOB51_CITY}&sortType=0"
        page.goto(url, timeout=45000, wait_until="domcontentloaded")

        # 等待页面 JS 执行完成
        time.sleep(random.uniform(3, 5))

        # 滚动页面触发懒加载
        for _ in range(3):
            page.mouse.wheel(0, random.randint(300, 800))
            time.sleep(0.5)

        # 方法1: 直接从 window.__SEARCH_RESULT__ 提取
        raw_jobs = page.evaluate("""() => {
            try {
                if (window.__SEARCH_RESULT__) {
                    const result = window.__SEARCH_RESULT__;
                    const jobs = result.engine_search_result || result.joblist || [];
                    return jobs.map(j => ({
                        title: j.job_name || '',
                        company: j.company_name || '',
                        salary: j.providesalary_text || j.salary || '',
                        location: j.workarea_text || j.attribute_text && j.attribute_text[0] || '成都',
                        tags: (j.attribute_text || []).join(' | ') + ' | ' + (j.jobwelf || ''),
                        link: j.job_href || '',
                        source: '51job'
                    }));
                }
            } catch(e) {}
            return null;
        }""")

        if raw_jobs:
            print(f"    [OK] __SEARCH_RESULT__ 提取到 {len(raw_jobs)} 个职位")
        else:
            # 方法2: 从 script 标签中正则提取 JSON
            print(f"    [INFO] __SEARCH_RESULT__ 为空，尝试 DOM 提取...")
            raw_jobs = page.evaluate("""() => {
                const jobs = [];
                // 尝试多种选择器
                const selectors = [
                    '.j_joblist', '.el', '.joblist-item',
                    '[class*="joblist"]', '[class*="job-item"]',
                    '.t1', '.job', '.el-item'
                ];
                let cards = [];
                for (const sel of selectors) {
                    cards = document.querySelectorAll(sel);
                    if (cards.length > 0) break;
                }
                
                if (cards.length === 0) {
                    // 最终降级：尝试所有包含职位名的链接
                    const allLinks = document.querySelectorAll('a[href*="jobs.51job.com"], a[href*="/job/"]');
                    allLinks.forEach(a => {
                        const href = a.href;
                        const text = a.textContent.trim();
                        if (text && text.length > 2 && text.length < 50) {
                            jobs.push({
                                title: text,
                                company: '',
                                salary: '',
                                location: '成都',
                                tags: '',
                                link: href,
                                source: '51job'
                            });
                        }
                    });
                    return jobs;
                }
                
                cards.forEach(card => {
                    const getText = (sel) => {
                        const el = card.querySelector(sel);
                        return el ? el.textContent.trim() : '';
                    };
                    const title = getText('.jname, .t1 a, [class*="jname"], a[href*="job"]');
                    const company = getText('.cname, [class*="cname"], [class*="company"]');
                    const salary = getText('.sal, [class*="sal"], [class*="salary"]');
                    const area = getText('.info .name, [class*="area"], .t3');
                    const tags = Array.from(card.querySelectorAll('.t1 .s, .d.at, [class*="tag"], .el'))
                        .map(e => e.textContent.trim()).filter(t => t).join(' | ');
                    const linkEl = card.querySelector('a[href*="job"], .jname a, a.t1');
                    const link = linkEl ? linkEl.href : '';
                    
                    if (title || company) {
                        jobs.push({
                            title: title,
                            company: company,
                            salary: salary,
                            location: area || '成都',
                            tags: tags,
                            link: link,
                            source: '51job'
                        });
                    }
                });
                return jobs;
            }""")
            print(f"    [DOM] 提取到 {len(raw_jobs)} 个职位")

        # 去重
        seen = set()
        for job in raw_jobs:
            key = f"{job.get('title','')}_{job.get('company','')}"
            if key not in seen and (job.get('title') or job.get('company')):
                seen.add(key)
                jobs.append(job)

        for j in jobs[:3]:
            print(f"    -> {j['title'][:30]} | {j.get('company','')[:20]} | {j.get('salary','')}")

    except Exception as e:
        print(f"    [ERROR] {e}")
    finally:
        browser.close()

    return jobs


# ============ 猎聘抓取（API + Playwright 降级） ============

def scrape_liepin_api(keyword):
    """直接调用猎聘 API 接口"""
    print(f"  [猎聘API] 搜索: {keyword}")
    jobs = []

    try:
        api_url = "https://api-c.liepin.com/api/com.liepin.searchfront4c.pc-search-job"
        headers = {
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.liepin.com",
            "Referer": f"https://www.liepin.com/zhaopin/?key={urllib.parse.quote(keyword)}&dqs={LIEPIN_CITY}",
            "X-Requested-With": "XMLHttpRequest",
        }
        payload = {
            "data": {
                "mainSearchPcConditionForm": {
                    "city": LIEPIN_CITY,
                    "dq": LIEPIN_CITY,
                    "currentPage": "0",
                    "pageSize": 40,
                    "key": keyword,
                    "workYearCode": "0",
                    "industry": "",
                    "salary": "",
                    "eduLevel": "",
                    "function": "",
                    "sortType": "0",
                },
                "passThroughForm": {
                    "scene": "init",
                    "ckId": "",
                    "skId": "",
                    "fkId": "",
                }
            }
        }
        resp = requests.post(api_url, json=payload, headers=headers, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            card_list = (
                data.get("data", {})
                .get("data", {})
                .get("jobCardList", [])
            )
            for card in card_list:
                job_info = card.get("job", {})
                comp_info = card.get("comp", {})
                title = job_info.get("title", "")
                company = comp_info.get("compName", "")
                salary = job_info.get("salary", "")
                location = job_info.get("dq", "成都")
                job_link = job_info.get("link", "")
                if job_link and not job_link.startswith("http"):
                    job_link = "https://www.liepin.com" + job_link

                labels = job_info.get("labels", [])
                comp_scale = comp_info.get("compScale", "")
                comp_industry = comp_info.get("industryName", "")
                comp_type = comp_info.get("compType", "")
                tags = " | ".join(labels) + f" | {comp_scale} | {comp_industry} | {comp_type}"

                if title or company:
                    jobs.append({
                        "title": title,
                        "company": company,
                        "salary": salary,
                        "location": location,
                        "tags": tags,
                        "link": job_link,
                        "source": "猎聘",
                        "comp_industry": comp_industry,
                        "comp_scale": comp_scale,
                        "comp_type": comp_type,
                    })
            print(f"    [OK] API 返回 {len(jobs)} 个职位")
        else:
            print(f"    [WARN] API 返回 {resp.status_code}，降级到浏览器抓取")

    except Exception as e:
        print(f"    [WARN] API 异常: {e}，降级到浏览器抓取")

    return jobs


def scrape_liepin_browser(playwright, keyword):
    """猎聘浏览器降级抓取"""
    print(f"  [猎聘浏览器] 搜索: {keyword}")
    jobs = []
    browser, context = create_browser_context(playwright)
    page = context.new_page()

    try:
        url = f"https://www.liepin.com/zhaopin/?key={keyword}&dqs={LIEPIN_CITY}"
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        time.sleep(random.uniform(4, 6))

        # 滚动触发加载
        for _ in range(4):
            page.mouse.wheel(0, random.randint(300, 800))
            time.sleep(random.uniform(0.5, 1.5))

        raw_jobs = page.evaluate("""() => {
            const jobs = [];
            // 猎聘多种选择器
            const selectors = [
                '.job-list-item', '[class*="job-item"]', '[class*="job-card"]',
                '[data-job-id]', '[class*="position-item"]', '.list-item',
                '.job-card-wrap', '[class*="job-card-wrap"]'
            ];
            let cards = [];
            for (const sel of selectors) {
                cards = document.querySelectorAll(sel);
                if (cards.length > 0) {
                    break;
                }
            }
            
            if (cards.length === 0) {
                // 降级：从所有链接提取
                const allLinks = document.querySelectorAll('a[href*="/job/"], a[href*="liepin.com/job"]');
                allLinks.forEach(a => {
                    const text = a.textContent.trim();
                    if (text && text.length > 2 && text.length < 60) {
                        jobs.push({
                            title: text,
                            company: '',
                            salary: '',
                            location: '成都',
                            tags: '',
                            link: a.href,
                            source: '猎聘'
                        });
                    }
                });
                return jobs;
            }
            
            cards.forEach(card => {
                const getText = (sel) => {
                    const el = card.querySelector(sel);
                    return el ? el.textContent.trim() : '';
                };
                const title = getText('.job-title, [class*="title"], h3, h2, [class*="job-title"]');
                const company = getText('.company-name, [class*="company"], [class*="corp"], [class*="comp-name"]');
                const salary = getText('.salary, [class*="salary"]');
                const location = getText('.area, [class*="area"], [class*="city"], [class*="dq"]');
                const tags = card.textContent.trim().substring(0, 500);
                const linkEl = card.querySelector('a[href*="/job/"], a[href*="liepin"], a');
                const link = linkEl ? linkEl.href : '';
                
                if (title || company) {
                    jobs.push({
                        title: title,
                        company: company,
                        salary: salary,
                        location: location || '成都',
                        tags: tags,
                        link: link,
                        source: '猎聘'
                    });
                }
            });
            return jobs;
        }""")

        for job in raw_jobs:
            if job.get('title') or job.get('company'):
                jobs.append(job)

        print(f"    [DOM] 提取到 {len(jobs)} 个职位")

    except Exception as e:
        print(f"    [ERROR] {e}")
    finally:
        browser.close()

    return jobs


def scrape_liepin(playwright, keyword):
    """猎聘抓取：先尝试 API，失败则用浏览器"""
    jobs = scrape_liepin_api(keyword)
    if not jobs:
        jobs = scrape_liepin_browser(playwright, keyword)
    return jobs


# ============ 智联招聘抓取 ============

def scrape_zhaopin(playwright, keyword):
    """抓取智联招聘"""
    print(f"  [智联] 搜索: {keyword}")
    jobs = []
    browser, context = create_browser_context(playwright)
    page = context.new_page()

    try:
        url = f"https://sou.zhaopin.com/?jl=801&kw={keyword}&p=1"
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        time.sleep(random.uniform(4, 6))

        # 滚动
        for _ in range(3):
            page.mouse.wheel(0, random.randint(300, 800))
            time.sleep(0.5)

        raw_jobs = page.evaluate("""() => {
            const jobs = [];
            const selectors = [
                '.joblist-box__item', '[class*="joblist"] [class*="item"]',
                '.positionList .joblist-box__item', '[class*="PositionList"]',
                '.jobCard', '[class*="job-card"]', '[class*="sou-job-item"]'
            ];
            let cards = [];
            for (const sel of selectors) {
                cards = document.querySelectorAll(sel);
                if (cards.length > 0) break;
            }
            
            if (cards.length === 0) {
                // 降级
                const allLinks = document.querySelectorAll('a[href*="/jobs/"], a[href*="zhaopin.com/job"]');
                allLinks.forEach(a => {
                    const text = a.textContent.trim();
                    if (text && text.length > 2 && text.length < 60) {
                        jobs.push({
                            title: text, company: '', salary: '',
                            location: '成都', tags: '', link: a.href, source: '智联'
                        });
                    }
                });
                return jobs;
            }
            
            cards.forEach(card => {
                const getText = (sel) => {
                    const el = card.querySelector(sel);
                    return el ? el.textContent.trim() : '';
                };
                jobs.push({
                    title: getText('.iteminfo__line2__jobname, [class*="jobname"], h3, [class*="title"]'),
                    company: getText('.iteminfo__line1__compname, [class*="compname"], [class*="company"]'),
                    salary: getText('.iteminfo__line2__jobdesc, [class*="salary"], [class*="jobdesc"]'),
                    location: getText('.iteminfo__line2__jobWidget, [class*="area"], [class*="city"]'),
                    tags: card.textContent.trim().substring(0, 500),
                    link: (card.querySelector('a') || {}).href || '',
                    source: '智联'
                });
            });
            return jobs;
        }""")

        for job in raw_jobs:
            if job.get('title') or job.get('company'):
                jobs.append(job)

        print(f"    提取到 {len(jobs)} 个职位")

    except Exception as e:
        print(f"    [ERROR] {e}")
    finally:
        browser.close()

    return jobs


# ============ 汇总抓取 ============

def scrape_all_jobs():
    """抓取所有关键词的职位"""
    all_jobs = []
    seen = set()

    with sync_playwright() as p:
        for i, keyword in enumerate(SEARCH_KEYWORDS, 1):
            print(f"\n[{i}/{len(SEARCH_KEYWORDS)}] 关键词: {keyword}")

            # 51job
            jobs_51 = scrape_51job(p, keyword)
            for j in jobs_51:
                key = f"{j.get('title','')}_{j.get('company','')}"
                if key not in seen:
                    seen.add(key)
                    all_jobs.append(j)

            # 猎聘
            jobs_lp = scrape_liepin(p, keyword)
            for j in jobs_lp:
                key = f"{j.get('title','')}_{j.get('company','')}"
                if key not in seen:
                    seen.add(key)
                    all_jobs.append(j)

            # 智联（每3个关键词抓一次，避免太多）
            if i % 3 == 0:
                jobs_zp = scrape_zhaopin(p, keyword)
                for j in jobs_zp:
                    key = f"{j.get('title','')}_{j.get('company','')}"
                    if key not in seen:
                        seen.add(key)
                        all_jobs.append(j)

            time.sleep(random.uniform(1, 3))

    print(f"\n去重后总计: {len(all_jobs)} 个职位")
    return all_jobs


# ============ GLM-4-Flash 智能筛选 ============

def filter_jobs_with_llm(jobs):
    """使用 GLM-4-Flash 对抓取的职位进行智能筛选"""
    if not jobs:
        return []

    # 分批处理（每批最多30个，避免 token 超限）
    BATCH_SIZE = 30
    all_filtered = []

    for batch_start in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[batch_start:batch_start + BATCH_SIZE]
        print(f"  筛选批次 {batch_start // BATCH_SIZE + 1}（{len(batch)} 个职位）...")

        jobs_text = ""
        for idx, job in enumerate(batch, 1):
            jobs_text += f"\n{idx}. 职位: {job.get('title', '')}\n"
            jobs_text += f"   公司: {job.get('company', '')}\n"
            jobs_text += f"   薪资: {job.get('salary', '')}\n"
            jobs_text += f"   地点: {job.get('location', '')}\n"
            jobs_text += f"   标签/要求: {job.get('tags', '')[:300]}\n"
            jobs_text += f"   来源: {job.get('source', '')}\n"
            if job.get('link'):
                jobs_text += f"   链接: {job['link']}\n"

        prompt = f"""以下是搜索到的成都地区职位信息，请严格按以下条件筛选：

【筛选条件 - 必须全部满足】
1. 领域匹配（满足任一即可）：
   - 气象/大气科学/气候/天气预报
   - 环境科学/环境工程/生态环境/环保
   - 大模型/LLM/AI agent/人工智能/机器学习/深度学习
   - 注意：职位名称不一定包含关键词，只要职责描述或技能要求中涉及上述领域就保留。
   - 例如"算法工程师"如果要求中提到大模型/NLP/AI，应该保留。

2. 专业要求：
   - 如果明确要求了学科专业，要求的专业中必须包含气象/大气科学/环境相关专业，否则排除。
   - 如果没有明确限制专业（专业不限/未提及），则保留。
   - "计算机相关专业"等不限制具体专业的，保留。

3. 公司类别：必须是国企/央企/外资/合资企业。
   - 民营、创业公司排除。
   - 如果无法判断公司类别，标注"待确认"并保留。
   - 银行/研究所/事业单位也保留。

4. 薪资：月薪不低于10000元。
   - "20-25万/年" = 约1.67-2.08万/月，符合
   - "1.3-2.6万" = 1.3-2.6万/月，符合
   - "面议"保留
   - "10-15K" = 1-1.5万/月，符合
   - 低于1万/月的排除

【职位数据】
{jobs_text}

请以JSON数组格式返回符合条件的职位，每个职位包含：
[{{"company":"公司名","company_type":"国企/央企/外资/合资/研究所/事业单位/待确认","location":"地点","requirements":"招聘要求（含专业要求+学历+技能+职责，尽量完整）","salary":"薪资范围","source_url":"来源链接（必须有）","source_site":"来源网站（51job/猎聘/智联）"}}]

注意：
- source_url 必须是上面职位数据中对应的链接
- requirements 字段请尽量完整汇总该职位的所有要求信息
- 如果没有符合条件的职位，返回 []
- 只返回JSON，不要其他文字"""

        try:
            resp = requests.post(
                ZHIPU_API_URL,
                headers={
                    "Authorization": f"Bearer {ZHIPU_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "glm-4-flash",
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "temperature": 0.1,
                },
                timeout=120
            )
            resp.raise_for_status()
            data = resp.json()
            content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            batch_filtered = parse_json_from_text(content)
            all_filtered.extend(batch_filtered)
            print(f"    本批筛选后: {len(batch_filtered)} 个")

        except Exception as e:
            print(f"    [ERROR] LLM筛选失败: {e}")

    return all_filtered


def parse_json_from_text(text):
    """从文本中提取 JSON 数组"""
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
    print(f"  [WARN] JSON解析失败")
    return []


# ============ HTML 报告生成 ============

def generate_html(jobs, date_display):
    """生成 HTML 报告 - 公司名可点击跳转、标注来源"""
    now_str = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')

    if not jobs:
        return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>成都招聘日报 - {date_display}</title>
<style>
body {{ font-family: 'Microsoft YaHei','Noto Sans CJK SC',sans-serif; margin:0; padding:20px; background:#f5f5f5; }}
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

        # 公司名可点击跳转
        if source_url and source_url.startswith('http'):
            company_link = f'<a href="{source_url}" target="_blank" class="company-link">{company}</a>'
        else:
            company_link = company

        # 来源标记
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
<div class="summary">共找到 <strong>{len(jobs)}</strong> 个符合条件岗位（国企/央企/外资/合资 | 薪资≥1万 | 气象/环境/大模型/AI相关）<br>
<span style="font-size:13px;color:#666;">点击公司名称可跳转到原始招聘页面</span></div>
<table><thead><tr>
<th>#</th><th>公司名称</th><th>公司类别</th><th>公司地点</th><th>招聘要求</th><th>薪资范围</th>
</tr></thead><tbody>
{chr(10).join(rows)}
</tbody></table>
<div class="footer">由 GitHub Actions + Playwright 自动生成 | 数据来源：51job/猎聘/智联 | {now_str}</div>
</div></body></html>"""


# ============ Server酱 通知 ============

def send_notification(title, desp):
    """通过 Server酱 推送微信通知"""
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
        print(f"[ERROR] 通知异常: {e}")


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

    print(f"{'='*50}")
    print(f"成都招聘日报 {date_display}")
    print(f"{'='*50}")

    # 第一步：抓取职位
    print(f"\n[1/4] 抓取招聘网站...")
    raw_jobs = scrape_all_jobs()

    if not raw_jobs:
        print("[WARN] 未抓取到任何职位数据")

    # 第二步：LLM 筛选
    print(f"\n[2/4] GLM-4-Flash 智能筛选...")
    filtered_jobs = filter_jobs_with_llm(raw_jobs)
    print(f"  筛选后: {len(filtered_jobs)} 个岗位")

    # 第三步：生成 HTML
    print(f"\n[3/4] 生成 HTML 报告...")
    os.makedirs(REPORT_DIR, exist_ok=True)
    filename = f"{date_str}.html" if filtered_jobs else "无.html"
    filepath = os.path.join(REPORT_DIR, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(generate_html(filtered_jobs, date_display))
    print(f"  报告: {filepath}")

    # 第四步：推送微信
    print(f"\n[4/4] 推送微信通知...")
    if filtered_jobs:
        title = f"成都招聘日报 {date_display}（{len(filtered_jobs)}个岗位）"
        lines = [f"今日共找到 **{len(filtered_jobs)}** 个符合条件岗位：\n"]
        for i, job in enumerate(filtered_jobs, 1):
            company = job.get('company', '未知')
            company_type = job.get('company_type', '')
            salary = job.get('salary', '')
            location = job.get('location', '')
            source_site = job.get('source_site', job.get('source', ''))
            source_url = job.get('source_url', '')
            requirements = job.get('requirements', '')[:80]

            # 带 source_url 的公司名做成链接
            if source_url and source_url.startswith('http'):
                company_str = f"[{company}]({source_url})"
            else:
                company_str = f"**{company}**"

            line = f"{i}. {company_str}（{company_type}）\n"
            line += f"   薪资: {salary} | 地点: {location} | 来源: {source_site}\n"
            if requirements:
                line += f"   要求: {requirements}...\n"
            lines.append(line)

        lines.append(f"\n完整报告: reports/{filename}")
        desp = '\n'.join(lines)
    else:
        title = f"成都招聘日报 {date_display} - 今日无符合条件岗位"
        desp = f"今日抓取到 {len(raw_jobs)} 个职位，经筛选后无符合条件岗位。\n明天继续关注。\n\n完整报告: reports/{filename}"
    send_notification(title, desp)

    print(f"\n{'='*50}")
    print("完成!")


if __name__ == '__main__':
    main()
