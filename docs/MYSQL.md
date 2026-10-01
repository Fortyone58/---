# MySQL本机使用说明

2026-10-01已将`C:\Users\XOS\Desktop\毕设\sol`的原SQLite演示数据迁入MySQL8.0.45。10张表112条记录保留，包含14账号、24岗位、11申请、8工时和13条提问历史。未重置演示数据。

## 日常体验

双击根目录“启动体验.bat”，访问http://127.0.0.1:5173。登录页选择身份即可自动填入模拟账号，公开演示密码为`Demo@2026`。学生student、单位library、资助中心aid、管理员admin_demo；其余账号见README。

启动会管理三个本机服务：MySQL13308、后端8000、前端5173。双击“停止服务.bat”会停止三个服务，数据仍保存。已验证重复启动、重复停止和重启后的数据保留。

这台电脑原有MySQL80服务运行于3306，项目没有修改它的账号、配置或进程。专用实例使用已安装的官方mysqld.exe，库名`qinghe_sol`，应用账号`qinghe_app`；管理账号仅用于本库备份及停止。连接凭据自动生成，仅在私有.env和忽略的备份配置中保存。

## 文件位置和配置

| 内容 | 位置 |
| --- | --- |
| 应用配置、数据库连接及凭据 | 项目根目录`.env` |
| MySQL数据、归属标记及服务器日志 | `.env`中的`MYSQL_RUNTIME_DIR`；ASCII路径，位于本机LocalAppData的QingheSol目录 |
| MySQL程序 | `C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqld.exe` |
| 应用进程记录及日志 | `work/runtime/` |
| SQL备份 | `work/backups/mysql-demo-时间.sql` |
| 原SQLite文件 | `data/campus_demo.db`，迁移后保留但不再写入 |
| 迁移时SQLite备份和原配置 | `docs/acceptance/mysql-migration.json`的`source_backup`所处目录 |
| 迁移、恢复和重启证据 | `docs/acceptance/mysql-*.json`及`REPORT-MySQL.md` |

Windows商店版应用可能把LocalAppData重定向到应用包LocalCache。以配置中的实际路径为准；启动会解析并核对真实目录。MySQL原生程序的数据路径需ASCII，因此没有放进含“毕设”的代码目录。

`.env`、数据库和`work/`均不提交Git。移动代码到另一个目录、换电脑或只复制项目文件夹，都不会自动迁移MySQL实例。需保留SQL备份并在目标MySQL导入；管理脚本要求项目及数据归属匹配，不会接管任意实例。

## 备份和复位

先启动体验，再在backend目录运行：

```powershell
uv run python ..\scripts\mysql_data.py backup
```

使用官方mysqldump的单事务导出，输出备份位置及SHA-256，不在命令行打印密码。本次迁移生成的SQL已在13307临时实例实际恢复；10张表全部行哈希与导出时一致。

双击“重置演示数据.bat”并输入`RESET`才执行复位：停止Web服务、启动专用MySQL、认证并核对数据归属、先导出SQL，再恢复固定模拟数据。失败时保留备份。复位会清除后续体验记录和提问；本轮没有对正式演示库执行。恢复功能已在隔离测试库验证，包括自关联工时更正链。

## 回退至迁移时SQLite

原SQLite停留在切换时的数据，不会跟随MySQL的新操作更新。以下回退只适用于返回迁移时快照；需要保留迁移后的新数据时，应继续修复MySQL或另行迁回，不能直接切换旧文件。

1. 先运行上述MySQL备份，保存切换后的数据。
2. 双击停止服务，保存当前`.env`的私有备份。
3. 打开`docs/acceptance/mysql-migration.json`，找到`source_backup`所在文件夹，把其中`original.env`复制回项目根目录`.env`。
4. 双击启动体验。原SQLite文件保留，启动检查`/api/ping`应显示`database: sqlite`。

不要把旧`.env`或SQL备份放到公开仓库或聊天交付包。后续需要切回已经有数据的专用MySQL时，恢复保存的MySQL配置即可；不要再次执行一次性迁移脚本。

## 开发验证

应用连接使用READ COMMITTED；名额、跨单位工时和更正链有行锁。schema0002使用显式DDL把旧MySQL时间列升级到DATETIME(6)，已测试重复执行和拒绝未知版本。此升级保留既有数据，但不会恢复旧DATETIME(0)已舍入的微秒。

`uv run pytest -q`仍使用隔离SQLite库；MySQL验证另起13307临时实例，数据库名以qinghe_verify_开头，运行结束停止，不测试正式13308库。Windows临时服务路径须ASCII：

```powershell
uv run python ..\scripts\verify_mysql.py --work-dir 'C:\Users\XOS\Documents\Codex\2026-09-30\c-users-xos-desktop-sol\work\mysql-verify' --junit-output ../docs/acceptance/mysql-tests-local.xml
```

需要验证迁移SQL恢复时，追加`--restore-demo-dump ..\docs\acceptance\mysql-migration.json`。这只把报告指向且哈希一致的SQL恢复到临时13307实例。

`migrate_to_mysql.py`是从本项目原SQLite迁往专用空库的一次性工具。要求后端停止、来源完整、目标无数据；保留SQLite和私有配置快照，比较所有行并完成SQL备份后才激活新配置。当前项目已迁移，该工具会拒绝再次迁移当前MySQL或覆盖非空库。

## 常见情况

- 页面打不开：双击启动体验，查看work/runtime；数据库状态可在backend运行`uv run python ..\scripts\mysql_runtime.py status`。
- 13308被其他程序占用、密码错误或数据目录归属不同：脚本会拒绝操作；核对私有配置及数据库日志。不要重置3306的root密码。
- MySQL数据目录丢失：启动会拒绝自动造一个空库覆盖现场。先找原目录或使用已保存SQL在新实例恢复。
- 检查数据库类型：访问http://127.0.0.1:8000/api/ping，应有`"database":"mysql"`；该接口不返回凭据。
