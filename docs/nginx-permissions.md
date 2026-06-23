# Storm Toolkit × Nginx 部署权限指南

目标：让 nginx（容器化或宿主机）能直接读取 `./data` 下的 JSON 文件，无需通过 Web API 中转。

---

## 问题根源（已修复）

Storm Toolkit 用 Python `tempfile.mkstemp` + `os.replace` 做原子写入。**POSIX 系统上 `mkstemp` 默认权限是 `0600`**（仅 owner 可读），即便容器跑 root、文件 owner 是 root，nginx 进程（通常以 `www-data` / `nginx` 用户运行）作为 "other" **读不到**。

代码层已修复：`src/storm_toolkit/storage.py:_atomic_write_json` 显式 `os.chmod(tmp_path, 0o644)`，所有写入的 JSON 现在都是 644。

**升级部署后建议先清空一次 `./data/tracks/*.json`，下一轮 scheduler 会以新权限重写。** 旧文件仍是 600，nginx 读不到。

---

## 权限模型

| 角色 | UID/GID（典型） | 需要的权限 |
|------|----------------|-----------|
| 容器内 Python（root） | UID 0 | 写 `./data`（755 即可） |
| 宿主机 nginx | `nginx`/`www-data`，UID 33 或 995 | 读 `./data/*.json`（文件需 ≥644） |
| nginx 容器 | 容器内默认 UID 101（alpine）或 33 | 同上 |

**关键规则**（POSIX）：
1. 文件 mode 必须 ≥ `0644`（other 可读）—— **已修复**
2. 目录 mode 必须 ≥ `0755`（other 可进入 + 列目录）—— Python `mkdir` 默认即此值
3. 从 nginx 进程到 `./data` 的**路径上每一级目录**都需要 `o+x`（可执行/可进入）

---

## 方案 A：宿主机已有 nginx，直接读 `./data`

适合：服务器上已经有 nginx 在跑其他站点，想把 `./data` 加成一个静态目录。

### 步骤

1. **部署 storm-toolkit**：
   ```bash
   cd /opt/storm-toolkit
   docker compose up -d
   # data 会落到 /opt/storm-toolkit/data
   ```

2. **确保目录链路可进入**（宿主机执行）：
   ```bash
   chmod o+x /opt /opt/storm-toolkit
   chmod -R o+rX /opt/storm-toolkit/data
   ```
   > 注意大写 `X`：只对目录加 x，不影响普通文件。

3. **nginx 配置**（`/etc/nginx/conf.d/storm-data.conf`）：
   ```nginx
   server {
       listen 80;
       server_name typhoon.example.com;

       # 直接 serve data 目录下的 JSON
       location /storm-data/ {
           alias /opt/storm-toolkit/data/;

           # 默认 Content-Type 为 JSON
           default_type application/json;

           # 允许跨域（如果前端在不同源）
           add_header Access-Control-Allow-Origin *;

           # 关闭 autoindex（生产）或开启（调试）
           autoindex off;

           # 大文件场景：tracks 可能到几百 KB
           sendfile on;
           tcp_nopush on;
       }

       # 单独暴露最新的活跃列表（前端首屏可优先拉这个）
       location = /storm-active.json {
           alias /opt/storm-toolkit/data/storms_active.json;
           default_type application/json;
           add_header Cache-Control "no-cache";
       }
   }
   ```

4. **重载 nginx**：
   ```bash
   nginx -t && systemctl reload nginx
   ```

5. **验证**：
   ```bash
   # 用 nginx 用户身份测试
   sudo -u www-data cat /opt/storm-toolkit/data/storms_active.json | head -c 100
   curl http://localhost/storm-data/storms_active.json
   ```

---

## 方案 B：在 docker-compose 内加 nginx 服务（推荐）

适合：从零部署，希望 nginx 与 storm-toolkit 同编排。

### docker-compose.yml 完整示例

```yaml
services:
  scheduler:
    build: .
    pull_policy: never
    image: storm-toolkit
    container_name: storm-scheduler
    restart: unless-stopped
    command: ["--schedule"]
    volumes:
      - ${DATA_HOST_DIR:-./data}:/app/data
    env_file: [.env]

  web:
    build: .
    pull_policy: never
    image: storm-toolkit
    container_name: storm-web
    restart: unless-stopped
    command: ["--web"]
    ports:
      - "127.0.0.1:19995:19995"   # 只监听本地，nginx 反代
    volumes:
      - ${DATA_HOST_DIR:-./data}:/app/data
    env_file: [.env]
    depends_on: [scheduler]

  # nginx 容器：反代 Web API + 直接 serve ./data 静态 JSON
  nginx:
    image: nginx:alpine
    container_name: storm-nginx
    restart: unless-stopped
    ports:
      - "80:80"
    volumes:
      - ${DATA_HOST_DIR:-./data}:/var/lib/storm-data:ro    # 只读挂载
      - ./nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on: [web]

# 不再需要顶层 volumes: 声明（绑定挂载）
```

**关键点**：
- nginx 容器挂 `./data` 时加 `:ro`，强制只读，nginx 用户 ID 与 storm-toolkit 不同也不影响
- `:ro` 模式下即使容器内 Python 把文件写成 666 也没问题，nginx 只是读

### nginx 配置（`./nginx/default.conf`）

```nginx
server {
    listen 80;
    server_name _;

    # 1. 直接静态读取 data JSON（零中转）
    location /storm-data/ {
        alias /var/lib/storm-data/;
        default_type application/json;
        add_header Access-Control-Allow-Origin *;
        add_header Cache-Control "no-cache";
        try_files $uri =404;
    }

    # 2. 反代 Web API（关注/取消关注/详情等动态操作）
    location /api/ {
        proxy_pass http://web:19995;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # 3. 前端首页
    location / {
        proxy_pass http://web:19995;
    }
}
```

### 启动

```bash
mkdir -p ./data ./nginx
# 把上面的 default.conf 放到 ./nginx/default.conf
docker compose up -d
curl http://localhost/storm-data/storms_active.json   # 测试
```

---

## 方案 C：分离的 nginx 容器（独立 compose）

适合：nginx 已有独立容器在跑，只是把 storm-toolkit 的 `./data` 加进来。

在现有 nginx 容器的 compose 里加一个 volume：

```yaml
nginx:
  image: nginx:alpine
  volumes:
    - /opt/storm-toolkit/data:/var/lib/storm-data:ro
    # ... 其他配置
```

然后在 nginx.conf 里 `location` 指向 `/var/lib/storm-data`，规则同方案 B。

---

## 关键检查清单

### 文件权限（容器跑 root 时）

```bash
# 在宿主机执行
ls -la /opt/storm-toolkit/data/
# 预期：
#   -rw-r--r--  root root  storms_active.json   (644)
#   -rw-r--r--  root root  watchlist.json       (644)
#   drwxr-xr-x  root root  tracks/              (755)
#   drwxr-xr-x  root root  history/             (755)
#   tracks/ 下：-rw-r--r-- ... *.json            (644)
```

如果发现 600 文件（旧数据）：
```bash
chmod -R o+rX /opt/storm-toolkit/data
# 或直接清空让 scheduler 重写
rm /opt/storm-toolkit/data/tracks/*.json
```

### 目录可进入（路径每级都要 o+x）

```bash
namei -l /opt/storm-toolkit/data/storms_active.json
# 输出的每一行都应有 r-x 权限给 other
```

### nginx 容器内的读权限测试

```bash
docker exec storm-nginx cat /var/lib/storm-data/storms_active.json | head -c 50
```

### nginx 用户身份测试（方案 A）

```bash
sudo -u nginx test -r /opt/storm-toolkit/data/storms_active.json && echo OK || echo FAIL
```

---

## SELinux / AppArmor（高级）

在 RHEL/CentOS/Fedora 等 SELinux 默认 Enforcing 的系统上，宿主机 nginx（`httpd_t`）默认**不允许**读容器挂载卷（`container_file_t`）。

```bash
# 查看 SELinux 标签
ls -Z /opt/storm-toolkit/data/

# 给目录打 httpd 可读标签
semanage fcontext -a -t httpd_sys_content_t "/opt/storm-toolkit/data(/.*)?"
restorecon -Rv /opt/storm-toolkit/data

# 或临时允许（不推荐生产）
setsebool -P httpd_read_user_content 1
```

AppArmor（Ubuntu）默认较宽松，一般不需要额外配置。

---

## UID/GID 高级控制（可选）

如果不想让容器跑 root，想以特定 UID 写文件：

### 方案 1：docker-compose `user` 指令

```yaml
scheduler:
  user: "1000:1000"   # 与宿主机 nginx 同 GID
  ...
```

需要：
- 容器内该 UID 对 `./data` 有写权限（宿主机 chown 调整）
- 写出的文件 owner=1000，nginx 加入 GID 1000 即可读（如果 mode 是 640）或 nginx 作为 other 读（mode 644）

### 方案 2：Docker user namespace remapping

```json
// /etc/docker/daemon.json
{
  "userns-remap": "default"
}
```

容器内 root 映射到宿主机一个高 UID 区间（如 100000+）。文件 owner 是该高 UID，需要把 nginx 加入对应组。这是企业级安全方案，配置较复杂。

---

## 推荐选择

| 场景 | 推荐方案 |
|------|---------|
| 个人服务器，nginx 已有 | **方案 A**（最省事） |
| 全新部署 | **方案 B**（compose 编排统一） |
| 已有 nginx 容器在跑 | **方案 C** |
| 企业/严格安全 | 方案 B + UID/GID 控制 |

---

## 故障排查速查

| 现象 | 可能原因 | 解决 |
|------|---------|------|
| `curl` 403 Forbidden | 文件 mode 600（旧数据） | `chmod -R o+rX ./data` |
| `curl` 403 | 目录缺 o+x | `chmod o+x` 路径每级 |
| `curl` 404 | `alias` 末尾缺 `/` | `alias /xxx/data/;` 必须以 `/` 结尾 |
| 偶发 404（旧文件可读、新文件不可读） | 新写入仍是 600 | 升级到最新代码（已修复 `_atomic_write_json`） |
| SELinux 系统下 403 | context 不对 | `restorecon -Rv ./data` |
| nginx 容器化下读不到 | 挂载缺 `:ro` 或路径不一致 | 用 `docker exec nginx ls /var/lib/storm-data` 验证 |
