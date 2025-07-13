# Discord 更新推流机器人

# Discord 更新推流机器人 (Update Feed Bot)

一个功能强大的 Discord 机器人，旨在帮助服务器内的创作者（如Mod作者、开发者、艺术家）更方便地向其关注者推送更新。它专为 Discord 的**论坛频道**设计，允许用户订阅特定帖子的更新，或关注某个作者的所有动态。

## ✨ 主要功能

- **帖子订阅**: 用户可以针对性地订阅某个帖子的“发行版”或“测试版”更新。
- **作者关注**: 用户可以关注自己喜欢的创作者，当该作者在指定论坛发布新帖子时，用户会收到通知。
- **灵活的通知系统**: 更新时会向所有订阅者和关注者发送通知。
- **私信控制面板**: 每个用户都可以通过与机器人私信，使用一个交互式的控制面板来：
  - 查看所有收到的更新通知。
  - 管理（查看和取消）所有已订阅的帖子。
  - 管理（查看和取消）所有已关注的作者。
- **管理员工具**: 提供查询机器人实时运行状态（CPU、内存、延迟等）的管理指令。
- **持久化存储**: 所有订阅和关注关系都存储在 MySQL 数据库中，确保数据安全。
- **容器化部署**: 使用 Docker 和 Docker Compose，实现一键部署和轻松维护。

## 🚀 指令列表

| 指令 | 目标用户 | 描述 | 使用范围 |
| :--- | :--- | :--- | :--- |
| `/创建更新推流` | 帖子作者 | 在一个论坛帖子内创建订阅和关注按钮，开启该帖子的更新功能。 | 指定的论坛频道 |
| `/更新推流` | 帖子作者 | 向所有订阅该帖子或关注该作者的用户推送一条更新通知。 | 已开启更新的帖子 |
| `/控制面板` | 所有用户 | 打开私人的订阅管理中心，查看更新、管理订阅和关注。 | 与机器人的私信 |
| `/bot 运行状态` | 机器人管理员 | 查询机器人当前的 CPU、内存、运行时长等详细状态。 | 服务器内 |

## 🛠️ 安装与部署

本项目设计为使用 Docker 进行部署，大大简化了环境配置的复杂性。

### 先决条件

- [Git](https://git-scm.com/)
- [Docker](https://www.docker.com/products/docker-desktop/)
- [Docker Compose](https://docs.docker.com/compose/install/)

### 配置步骤

1.  **克隆仓库**
    ```bash
    git clone <你的仓库URL>
    cd <仓库目录>
    ```

2.  **创建 Discord Bot**
    - 前往 [Discord Developer Portal](https://discord.com/developers/applications)。
    - 点击 "New Application" 创建一个新的应用。
    - 进入 "Bot" 页面，点击 "Add Bot"。
    - **获取 Token**: 在 Bot 页面点击 "Reset Token" 来获取你的机器人令牌。**这是非常敏感的信息，绝对不要泄露给任何人！**
    - **开启特权意图 (Privileged Intents)**: 确保 `GUILD MEMBERS INTENT` 是**关闭**的，因为代码中并未请求该权限，保持最小权限原则。代码中 `intents.guilds = True` 是默认权限，无需开启特权。

3.  **邀请机器人到你的服务器**
    - 在 Developer Portal 中，进入 "OAuth2" -> "URL Generator"。
    - 在 "Scopes" 中选择 `bot` 和 `applications.commands`。
    - 在 "Bot Permissions" 中，授予以下必要的权限：
      - `View Channels`
      - `Send Messages`
      - `Send Messages in Threads`
      - `Embed Links`
      - `Read Message History`
    - 复制生成的URL，在浏览器中打开，然后将机器人邀请到你的目标服务器。

4.  **配置环境变量**
    - 将项目中的 `.env.example.txt` 文件复制一份，并重命名为 `.env`。
    - 使用文本编辑器打开 `.env` 文件，并根据文件内的注释填入你自己的配置信息。这包括：
      - 你的 Discord Bot Token (`DISCORD_TOKEN`)
      - 你的服务器 ID (`TARGET_GUILD_ID`)
      - 机器人管理员的用户 ID (`ADMIN_IDS`)
      - 允许使用机器人的论坛频道 ID (`ALLOWED_CHANNELS`)
      - 数据库密码等。

### 运行机器人

1.  **启动服务**
    在你的项目根目录下，运行以下命令来构建并启动机器人和数据库容器：
    ```bash
    docker-compose up -d --build
    ```
    Docker Compose 会自动拉取 MySQL 镜像，构建机器人镜像，并创建持久化数据卷。

2.  **查看日志**
    如果你想查看机器人的实时输出或排查问题，可以使用以下命令：
    ```bash
    docker-compose logs -f
    ```

3.  **停止服务**
    要停止机器人和数据库，运行：
    ```bash
    docker-compose down
    ```
    使用 `-v` 选项会删除数据库数据卷，请谨慎操作：`docker-compose down -v`。

## ⚠️ 重要风险提示

### 幽灵提及 (Ghost Ping) 的滥用风险

`/更新推流` 功能通过在短时间内发送并删除包含用户提及的消息（即“幽灵提及”）来通知用户。

- **高风险**: **此行为极易被 Discord 的 API 速率限制系统检测为滥用行为**。如果通知的用户数量庞大，高频率的 API 调用（发送、删除、编辑）可能导致你的机器人被 **临时甚至永久封禁**。
- **建议**:
  1.  **强烈建议禁用此功能**：在 `.env` 文件中将 `UPDATE_MENTION_DELAY` 设置为一个较大的值（如 `5000`，即5秒），或在代码中移除提及和删除消息的逻辑。
  2.  **替代方案**: 最安全、最稳定的通知方式是通过 **私信** 通知用户。本机器人已包含私信面板框架，可以基于此进行扩展。

### 数据库 `ON DELETE CASCADE` 风险

旧版代码中可能使用了 `ON DELETE CASCADE`。此功能虽然方便，但极其危险，一个用户的删除操作可能引发连锁反应，导致大量相关数据（如所有人的订阅记录）被意外清空。请确保你的数据库表结构已规避此风险（例如，使用 `ON DELETE SET NULL` 或 `ON DELETE RESTRICT`）。

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