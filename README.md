# 成都气象环境AI招聘信息日报

每天自动搜索成都地区气象/环境/大模型/AI agent 领域招聘信息，按条件筛选后生成 HTML 报告并推送微信通知。

## 运行在 GitHub Actions 上，完全免费，电脑关机也能运行。

### 技术方案：Playwright 无头浏览器 + GLM-4-Flash 智能筛选

---

## 部署步骤（约 10 分钟）

### 第一步：创建 GitHub 仓库

1. 登录 GitHub
2. 点击右上角 **+** → **New repository**
3. 仓库名填 `chengdu-job-tracker`
4. 选择 **Public**（公开仓库免费额度无限）
5. 勾选 **Add a README file**
6. 点击 **Create repository**

### 第二步：上传项目文件

将以下所有文件上传到仓库：

```
chengdu-job-tracker/
├── .github/workflows/daily-jobs.yml   ← GitHub Actions 工作流
├── main.py                             ← 主脚本（Playwright + GLM-4-Flash）
├── requirements.txt                    ← Python 依赖
├── .gitignore
└── reports/                            ← 报告输出目录（含 .gitkeep）
```

**上传方法：**
1. 在仓库页面点击 **Add file** → **Upload files**
2. 拖入 main.py、requirements.txt、.gitignore
3. 对于 `.github/workflows/daily-jobs.yml`：
   - 点击 **Add file** → **Create new file**
   - 文件名输入 `.github/workflows/daily-jobs.yml`
   - 粘贴内容，提交
4. 创建 `reports/.gitkeep`（空文件）

### 第三步：配置 Secrets（密钥）

1. 进入仓库 **Settings** → **Secrets and variables** → **Actions**
2. 点击 **New repository secret**，添加：

| Name | Value |
|------|-------|
| `ZHIPU_API_KEY` | 智谱 API Key |
| `SERVERCHAN_KEY` | Server酱 SendKey |

### 第四步：启用并测试

1. 进入 **Actions** 标签页
2. 如有提示，点击 **I understand my workflows, go ahead and enable them**
3. 点击 **成都招聘日报** → **Run workflow** → **Run workflow**
4. 等待执行完成（约 5-8 分钟），查看日志
5. 检查 `reports/` 目录是否生成了 HTML 文件
6. 检查微信是否收到通知

---

## 查看报告

- **GitHub 直接查看**：仓库 → `reports/` → 点击 HTML 文件 → **Preview**
- **GitHub Pages**（可选）：Settings → Pages → Source: main/root → 访问 `https://用户名.github.io/chengdu-job-tracker/reports/日期.html`

---

## 修改配置

### 执行时间
编辑 `.github/workflows/daily-jobs.yml`：
```yaml
cron: '30 13 * * *'  # 21:30 北京时间
# 改为每周一三五: '30 13 * * 1,3,5'
```

### 搜索关键词
编辑 `main.py` 中的 `SEARCH_KEYWORDS`：
```python
SEARCH_KEYWORDS = ["气象", "大气科学", "大模型", "环境科学", "AI agent", "人工智能 算法"]
```

---

## 费用

| 项目 | 费用 |
|------|------|
| GitHub Actions（公开仓库） | 完全免费 |
| 智谱 GLM-4-Flash API | 免费 |
| Playwright（无头浏览器） | 免费 |
| Server酱 免费版 | 免费 5 条/天 |
| **总计** | **0 元** |

---

## 技术架构

```
GitHub Actions (cron 定时触发)
    ↓
Playwright 无头浏览器
    ├── 51job (前程无忧) → 抓取职位卡片
    └── 猎聘 (liepin) → 抓取职位列表
    ↓
GLM-4-Flash 智能筛选
    (专业要求 / 公司类别 / 薪资 / 领域匹配)
    ↓
生成 HTML 报告 → 保存到 reports/ 目录
    ↓
Server酱 → 推送微信通知
    ↓
git commit + push → 报告存入仓库
```
