#!/usr/bin/env python3
"""
成都气象环境AI招聘信息日报 - Playwright + GLM-4-Flash 方案
使用 Playwright 无头浏览器抓取招聘网站，用 GLM-4-Flash 智能筛选。
"""

import os
import sys
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

# ============ 配置 ============
ZHIPU_API_KEY = os.environ.get('ZHIPU_API_KEY', '')
SERVERCHAN_KEY = os.environ.get('SERVERCHAN_KEY', '')
REPORT_DIR = os.environ.get('REPORT_DIR', 'reports')
ZHIPU_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

# 搜索关键词
SEARCH_KEYWORDS = ["气象", "大气科学", "大模型", "环境科学", "AI agent", "人工智能 算法"]

# 51job 城市代码：090200 = 成都
JOB51_CITY = "090200"


# ============ 招聘网站抓取 ============

def scrape_51job(playwright, keyword):
    """抓取前程无忧 (51job)"""
    print(f"  [51job] 搜索: {keyword}")
    jobs = []
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="zh-CN",
    )
    page = context.new_page()

    try:
        url = f"https://we.51job.com/pc/search?keyword={keyword}&jobArea={JOB51_CITY}&sortType=0"
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(4)

        # 用 JavaScript 提取数据（更可靠）
        raw_jobs = page.evaluate("""() => {
            const jobs = [];
            // 51job 职位卡片
            const cards = document.querySelectorAll('.j_joblist, [class*="j_joblist"]');
            cards.forEach(card => {
                const getText = (sel) => {
                    const el = card.querySelector(sel);
                    return el ? el.textContent.trim() : '';
                };
                const title = getText('.jname, [class*="jname"], .t1 a');
                const company = getText('.cname, [class*="cname"]');
                const salary = getText('.sal, [class*="sal"]');
                const area = getText('.info .name, [class*="area"]');
                const tags = Array.from(card.querySelectorAll('.t1 .s, .d.at, [class*="tag"]'))
                    .map(e => e.textContent.trim()).filter(t => t);
                
                // 职位详情链接
                const linkEl = card.querySelector('.jname a, a.t1, [class*="jname"] a');
                const link = linkEl ? linkEl.href : '';
                
                if (title || company) {
                    jobs.push({
                        title: title,
                        company: company,
                        salary: salary,
                        location: area || '成都',
                        tags: tags.join(' | '),
                        link: link,
                        source: '51job'
                    });
                }
            });
            return jobs;
        }""")

        # 去重（同公司同职位只保留一条）
        seen = set()
        for job in raw_jobs:
            key = f"{job['title']}_{job['company']}"
            if key not in seen and (job['title'] or job['company']):
                seen.add(key)
                jobs.append(job)

        print(f"    提取到 {len(jobs)} 个职位")
        for j in jobs[:3]:
            print(f"    -> {j['title'][:30]} | {j['company'][:20]} | {j['salary']}")

    except Exception as e:
        print(f"    [ERROR] {e}")
    finally:
        browser.close()

    return jobs


def scrape_liepin(playwright, keyword):
    """抓取猎聘"""
    print(f"  [猎聘] 搜索: {keyword}")
    jobs = []
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="zh-CN",
    )
    page = context.new_page()

    try:
        url = f"https://www.liepin.com/zhaopin/?key={keyword}&dqs=280"
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(5)

        # 用 JavaScript 提取所有可能的职位卡片
        raw_jobs = page.evaluate("""() => {
            const jobs = [];
            // 尝试多种选择器
            const selectors = [
                '.job-list-item', '[class*="job-item"]', '[class*="job-card"]',
                '[data-job-id]', '[class*="position-item"]', '.list-item'
            ];
            let cards = [];
            for (const sel of selectors) {
                cards = document.querySelectorAll(sel);
                if (cards.length > 0) break;
            }
            
            if (cards.length === 0) {
                // 降级：从页面文本提取
                const bodyText = document.body.innerText;
                return [{title: '__TEXT_FALLBACK__', text: bodyText.substring(0, 5000)}];
            }
            
            cards.forEach(card => {
                const getText = (sel) => {
                    const el = card.querySelector(sel);
                    return el ? el.textContent.trim() : '';
                };
                jobs.push({
                    title: getText('.job-title, [class*="title"], h3, h2'),
                    company: getText('.company-name, [class*="company"], [class*="corp"]'),
                    salary: getText('.salary, [class*="salary"]'),
                    location: getText('.area, [class*="area"], [class*="city"]'),
                    tags: card.textContent.trim().substring(0, 300),
                    link: '',
                    source: '猎聘'
                });
            });
            return jobs;
        }""")

        for job in raw_jobs:
            if job.get('title') == '__TEXT_FALLBACK__':
                # 文本降级模式：把页面文本传给 LLM 分析
                jobs.append({
                    'title': '(猎聘页面文本)',
                    'company': '',
                    'salary': '',
                    'location': '成都',
                    'tags': job.get('text', '')[:2000],
                    'link': '',
                    'source': '猎聘(文本)'
                })
            elif job.get('title') or job.get('company'):
                jobs.append(job)

        print(f"    提取到 {len(jobs)} 个职位")

    except Exception as e:
        print(f"    [ERROR] {e}")
    finally:
        browser.close()

    return jobs


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
                key = f"{j['title']}_{j['company']}"
                if key not in seen:
                    seen.add(key)
                    all_jobs.append(j)

            # 猎聘
            jobs_lp = scrape_liepin(p, keyword)
            for j in jobs_lp:
                key = f"{j['title']}_{j['company']}"
                if key not in seen:
                    seen.add(key)
                    all_jobs.append(j)

            # 关键词间稍作等待，避免被限制
            time.sleep(2)

    print(f"\n去重后总计: {len(all_jobs)} 个职位")
    return all_jobs


# ============ GLM-4-Flash 智能筛选 ============

def filter_jobs_with_llm(jobs):
    """使用 GLM-4-Flash 对抓取的职位进行智能筛选"""
    if not jobs:
        return []

    # 准备职位数据文本
    jobs_text = ""
    for i, job in enumerate(jobs, 1):
        jobs_text += f"\n{i}. 职位: {job.get('title', '')}\n"
        jobs_text += f"   公司: {job.get('company', '')}\n"
        jobs_text += f"   薪资: {job.get('salary', '')}\n"
        jobs_text += f"   地点: {job.get('location', '')}\n"
        jobs_text += f"   标签/要求: {job.get('tags', '')[:200]}\n"
        jobs_text += f"   来源: {job.get('source', '')}\n"
        if job.get('link'):
            jobs_text += f"   链接: {job['link']}\n"

    prompt = f"""以下是搜索到的成都地区职位信息，请严格按以下条件筛选：

【筛选条件 - 必须全部满足】
1. 领域：职位涉及气象/大气科学/环境/大模型(LLM)/AI agent/人工智能等任一领域。职位名称不一定包含关键词，只要职责描述或技能要求中涉及上述领域就保留。
2. 专业要求：
   - 如果明确要求了学科专业，要求的专业中必须包含气象/大气科学/环境相关专业（大气科学、气象学、应用气象学、气候学、环境科学、环境工程、生态学等），否则排除。
   - 如果没有明确限制专业（专业不限/未提及），则保留。
3. 公司类别：必须是国企/央企/外资/合资企业。民营、创业公司排除。如果无法判断公司类别，标注"待确认"并保留。
4. 薪资：月薪不低于10000元。
   - "20-25万/年" = 约1.67-2.08万/月，符合
   - "1.3-2.6万" = 1.3-2.6万/月，符合
   - "面议"保留
   - 低于1万/月的排除

【职位数据】
{jobs_text}

请以JSON数组格式返回符合条件的职位：
[{{"company":"公司名","company_type":"国企/央企/外资/合资/待确认","location":"地点","requirements":"招聘要求（含专业要求+学历+技能+职责）","salary":"薪资范围","source_url":"来源链接"}}]

注意：requirements 字段请尽量完整地汇总该职位的所有要求信息。
如果没有符合条件的职位，返回 []。只返回JSON，不要其他文字。"""

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
        return parse_json_from_text(content)
    except Exception as e:
        print(f"  [ERROR] LLM筛选失败: {e}")
        return []


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
    """生成 HTML 报告"""
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

        type_class = 'type-other'
        if '国企' in company_type or '央企' in company_type:
            type_class = 'type-guoqi'
        elif '外资' in company_type or '外企' in company_type:
            type_class = 'type-waizi'
        elif '合资' in company_type:
            type_class = 'type-hezi'

        link = f'<a href="{source_url}" target="_blank">查看详情</a>' if source_url and source_url.startswith('http') else '无'

        rows.append(f"""<tr>
<td>{i}</td><td>{company}</td>
<td><span class="company-type {type_class}">{company_type}</span></td>
<td>{location}</td><td>{requirements}</td>
<td class="salary">{salary}</td><td>{link}</td></tr>""")

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
th,td {{ padding:12px; text-align:left; border:1px solid #ddd; font-size:14px; }}
th {{ background:#4CAF50; color:white; white-space:nowrap; }}
tr:nth-child(even) {{ background:#f9f9f9; }}
tr:hover {{ background:#f0f0f0; }}
.salary {{ color:#e65100; font-weight:bold; white-space:nowrap; }}
.company-type {{ display:inline-block; padding:2px 8px; border-radius:3px; font-size:12px; white-space:nowrap; }}
.type-guoqi {{ background:#ffe0b2; color:#e65100; }}
.type-waizi {{ background:#e3f2fd; color:#1565c0; }}
.type-hezi {{ background:#f3e5f5; color:#7b1fa2; }}
.type-other {{ background:#f5f5f5; color:#666; }}
a {{ color:#1976d2; text-decoration:none; }}
a:hover {{ text-decoration:underline; }}
.footer {{ margin-top:30px; text-align:center; color:#bbb; font-size:12px; }}
</style></head><body><div class="container">
<h1>成都招聘日报 - {date_display}</h1>
<div class="summary">共找到 <strong>{len(jobs)}</strong> 个符合条件岗位（国企/央企/外资/合资 | 薪资≥1万 | 气象/环境相关专业或不限专业）</div>
<table><thead><tr>
<th>#</th><th>公司名称</th><th>公司类别</th><th>公司地点</th><th>招聘要求</th><th>薪资范围</th><th>来源</th>
</tr></thead><tbody>
{chr(10).join(rows)}
</tbody></table>
<div class="footer">由 GitHub Actions + Playwright 自动生成 | 数据来源：51job/猎聘 | {now_str}</div>
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
        title = f"成都招聘日报 {date_display}"
        lines = [f"今日共找到 **{len(filtered_jobs)}** 个符合条件岗位：\n"]
        for i, job in enumerate(filtered_jobs, 1):
            lines.append(f"{i}. **{job.get('company','未知')}**（{job.get('company_type','')}）| {job.get('salary','')} | {job.get('location','')}")
        lines.append(f"\n完整报告: reports/{filename}")
        desp = '\n'.join(lines)
    else:
        title = f"成都招聘日报 {date_display} - 今日无符合条件岗位"
        desp = "今日未发现符合条件的岗位，明天继续关注。"
    send_notification(title, desp)

    print(f"\n{'='*50}")
    print("完成!")


if __name__ == '__main__':
    main()
