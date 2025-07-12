# Discord 更新推流机器人

一个功能强大、基于Docker部署的Discord机器人。它允许帖子（Thread）作者为其内容建立一个灵活的订阅系统，并在发布更新时，通过“幽灵提及”精准地通知订阅了该帖子或关注了该作者的用户。

## 核心功能

- **基于帖子的订阅系统**: 用户可以在指定的论坛频道帖子中，通过点击按钮来订阅不同类型的更新。
- **灵活的订阅选项**:
  - **订阅发行版 (Release)**: 只接收该帖子的正式版更新通知。
  - **订阅测试版 (Test)**: 只接收该帖子的测试版更新通知。
  - **关注作者**: 接收该作者**所有**帖子的**所有类型**更新通知。
- **权限控制**: 只有帖子的创建者（作者）有权为该帖子开启或发布更新。
- **精准提及**: 使用“幽灵提及”特性，在不污染频道消息的情况下，确保用户能收到通知。
- **分批发送**: 自动将需要提及的大量用户进行分批，避免超出Discord单条消息的提及上限，并防止API速率限制。
- **实时状态更新**: 在推送更新期间，机器人会实时编辑一条状态消息，显示通知进度。
- **管理员指令**: 提供带权限控制的管理指令，用于监控机器人自身的性能状态。
- **高可配置性**: 所有关键参数（如Token、服务器ID、频道ID、消息文本等）均可通过 `.env` 文件进行配置。

## 技术栈

- **语言**: Python 3.11
- **核心框架**: `discord.py`
- **数据库**: MySQL 8.0
- **部署方案**: Docker & Docker Compose
- **性能监控**: `psutil`

## 部署指南

本项目被设计为通过Docker进行部署，这确保了环境的一致性和部署的便捷性。

### 前提条件

- [Git](https://git-scm.com/)
- [Docker](https://www.docker.com/products/docker-desktop/)
- [Docker Compose](https://docs.docker.com/compose/install/)

### 步骤 1: 克隆项目

```bash
git clone <你的项目仓库URL>
cd <项目目录>
```

### 步骤 2: 创建Discord应用

1.  前往 [Discord Developer Portal](https://discord.com/developers/applications)。
2.  点击右上角的 **"New Application"** 创建一个新应用。
3.  进入 **"Bot"** 标签页，点击 **"Add Bot"**。
4.  在 **"Privileged Gateway Intents"** 部分，开启以下两个权限：
    - ✅ **PRESENCE INTENT**
    - ✅ **MESSAGE CONTENT INTENT**
5.  在Bot页面顶部，点击 **"Reset Token"** 来获取您的机器人令牌（Token），并**立即**将其复制下来。

### 步骤 3: 配置环境变量

项目根目录下应该有一个 `.env.example` 文件。请复制它并重命名为 `.env`。

```bash
cp .env.example .env
```

然后，编辑 `.env` 文件，填入您自己的配置信息。

```dotenv
# 你的discord bot token (从步骤2获取):
DISCORD_TOKEN=在此填入你的机器人令牌

# 你的服务器的 ID
TARGET_GUILD_ID=在此填入你的Discord服务器ID

# 管理员ID，多个以英文半角逗号分割，允许查询运行状态
ADMIN_IDS=在此填入你的用户ID

#---更新推流---
# 更新推流允许使用的论坛channel id，以英文半角逗号分割
ALLOWED_CHANNELS=在此填入允许使用机器人的论坛频道ID

#嵌入式消息的设置:
EMBED_TITLE="更新推流Bot"
EMBED_TEXT="按底下的按钮更改你的订阅"
EMBED_ERROR="索引创建失败..."

#更新消息的设置{{text}}为作者附带的简单更新log，{{url}}为具体的更新链接，{{author}}为作者的名字:
UPDATE_TITLE="【更新】"
UPDATE_TEXT="您关注的帖子发布了一个更新:\n{{author}}->{{text}}\n你可以在这里查看: {{url}}"
UPDATE_ERROR="UNKOWN ERROR: 更新失败，请联系开发者"
UPDATE_MENTION_MAX_NUMBER=50 #单次消息提及的人数

#-------------MYSQL配置-------------
# 通常保持默认即可，Docker Compose会自动处理网络
MYSQL_HOST=db
MYSQL_USER=discord_bot
MYSQL_PASSWORD=请设置一个非常健壮的密码
MYSQL_ROOT_PASSWORD=请为root设置另一个非常健壮的密码
MYSQL_DATABASE=discord_bot_db
MYSQL_PORT=3306
#数据库连接池大小，可以根据实际需要调整，不可为空
POOL_SIZE=10
```

### 步骤 4: 构建并启动服务

在项目根目录下，运行以下命令：

```bash
# 构建镜像并以后台分离模式启动所有服务
docker compose up -d --build
```

第一次构建可能需要一些时间，因为它需要下载基础镜像并安装所有依赖。

### 步骤 5: 邀请机器人到服务器

1.  回到 [Discord Developer Portal](https://discord.com/developers/applications) 并选择您的应用。
2.  进入 **"OAuth2"** -> **"URL Generator"**。
3.  在 **"SCOPES"** 列表中，勾选以下两项：
    - `bot`
    - `applications.commands`
4.  在下方出现的 **"BOT PERMISSIONS"** 列表中，选择机器人需要的权限（为方便起见，可以先选择“管理员”）。
5.  复制页面底部生成的URL，将其粘贴到浏览器中打开，然后选择您的服务器并授权。

### 步骤 6: 查看运行日志

您可以使用以下命令来实时查看机器人应用的日志输出：

```bash
docker compose logs -f app```

## 指令用法

- `/创建更新推流`
  - **作用**: 为一个帖子开启更新订阅功能，并发送带有订阅按钮的面板。
  - **权限**: 只有该帖子的创建者（作者）可以使用。
  - **用法**: 在一个指定的论坛频道的帖子里直接输入此指令。

- `/更新推流 [update_type] [url] [message]`
  - **作用**: 发布一个新更新，并通知所有相关的订阅者。
  - **权限**: 只有该帖子的创建者（作者）可以使用。
  - **参数**:
    - `update_type` (必选): 更新类型，选择“发行版(Release)”或“测试版(Test)”。
    - `url` (必选): 指向本次更新的具体帖子楼层或页面的链接。
    - `message` (可选): 一段简短的更新描述，不超过400字。

- `/bot 运行状态`
  - **作用**: 查询机器人当前的CPU、内存、延迟等性能指标。
  - **权限**: 只有在 `.env` 文件中 `ADMIN_IDS` 列表里的用户可以使用。

## 未来优化方向

- **切换到SQLite**: 对于当前的应用规模，使用嵌入式的SQLite数据库可以完全替代独立的MySQL容器，从而**大幅降低内存占用**（可节省约400MB），并简化部署。
- **优化Dockerfile**:
  - **使用Alpine基础镜像**: 将 `FROM python:3.11-slim-bookworm` 切换为 `FROM python:3.11-alpine` 可以显著减小镜像体积和基础内存占用。
  - **采用多阶段构建**: 分离编译环境和运行环境，确保最终的生产镜像极度纯净、轻量和安全。

## 贡献

欢迎提交PRs和Issues来改进这个项目！

---

## 许可证 (License)

该项目采用 **知识共享署名-相同方式共享 4.0 国际许可协议 (CC BY-SA 4.0)** 进行许可。

[![CC BY-SA 4.0][cc-by-sa-shield]][cc-by-sa]

这意味着您可以自由地：
- **共享** — 在任何媒介以任何形式复制、发行本作品。
- **演绎** — 修改、转换或以本作品为基础进行创作，在任何用途下，甚至商业用途。

只要您遵守下列许可条款：
- **署名 (Attribution)** — 您必须给出适当的署名，提供指向本许可协议的链接，同时标明是否对原始作品作了修改。
- **相同方式共享 (ShareAlike)** — 如果您再混合、转换、或者基于本作品进行创作，您必须基于与原先许可协议相同的许可协议分发您的贡献。

详情请参阅 [CC BY-SA 4.0 许可协议全文](http://creativecommons.org/licenses/by-sa/4.0/)。

[cc-by-sa]: http://creativecommons.org/licenses/by-sa/4.0/
[cc-by-sa-shield]: https://img.shields.io/badge/License-CC%20BY--SA%204.0-lightgrey.svg