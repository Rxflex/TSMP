# TSMP launcher: Simplified Chinese. See en.ftl for comments on each message.

language-name = 简体中文

## Side rail

step-role = 创建或加入
step-sync = 同步模组和存档
step-launch = 启动游戏
step-load = 载入共享存档
step-play = 一起游玩

rail-game = 模拟人生 4 { $version }
rail-game-unknown = 模拟人生 4，版本未知
language = 语言

## Before the game is found

loading-title = 正在查找《模拟人生 4》
loading-body = 请稍候。
fatal-title = 未找到《模拟人生 4》
fatal-hint = 请安装游戏并启动一次，然后重新打开 TSMP。

## Home

home-title = 一起玩《模拟人生 4》
home-lead = 两位玩家在同一网络中打开 TSMP。一人创建会话，另一人加入。
home-host = 创建会话
home-host-detail = 把你的模组和存档分享给另一位玩家。
home-join = 加入会话
home-join-detail = 在此网络中找到主机并复制对方的模组。
home-computer = 这台电脑
fact-game = 游戏
fact-mods = 模组
fact-packs = 资料包
packs-none = 仅基础游戏
packs-count =
    { $n ->
       *[other] { $n } 个资料包
    }
script-mods-off = 脚本模组已关闭。请在游戏中打开“游戏选项”→“其他”，勾选“启用自定义内容和模组”和“允许脚本模组”，然后重启游戏。

## Hosting

host-setup-title = 选择要共享的存档
host-setup-lead = 另一位玩家会得到一份副本，对方自己的存档不受影响。
save-none = 不共享存档
save-none-detail = 一起开始新游戏
save-variants = 此家庭的存档：{ $n }
host-start = 开始创建
host-reading-title = 正在读取 Mods 文件夹
host-reading-body = 首次会检查每个文件，文件夹较大时可能需要几分钟。
host-failed-title = 会话创建失败
host-title = 会话已开启
host-lead = 游玩期间请保持此窗口打开。
host-address = 地址
host-mods = 模组
host-mods-value =
    { $files ->
       *[other] { $files } 个文件，{ $size }
    }
host-save = 存档
host-save-none = 无，新游戏
host-mod-missing = Mods 文件夹中没有 tsmp.ts4script，另一位玩家将收不到 TSMP 模组。
host-rivals-moved = 其他联机模组无法与 TSMP 同时运行，已从 Mods 移到 Mods.tsmp-backup：{ $names }
players = 玩家
players-empty = 正在等待另一位玩家。对方需要打开 TSMP，选择“加入会话”，然后点选这台电脑。
guest-connected = 已连接
guest-syncing = 正在复制 { $done } / { $total }
guest-ready = 已就绪
guest-failed = 失败：{ $message }
host-stop = 关闭会话
online = 通过互联网
online-detail = 供你网络之外的玩家使用。输入 TSMP 会话服务器的地址，它会给你一个邀请，发给对方即可。
online-open = 打开
online-bad-server = 这不是服务器地址。它形如 play.example.org 或 203.0.113.5:47810。
# { $server } is the session server, such as play.example.org
online-connecting = 正在连接 { $server }…
online-ready = 已在 { $server } 上打开。对方用这个邀请加入：
# { $reason } is the error, in English
online-retrying = 暂时无法连接服务器（{ $reason }）。正在重试；邀请保持不变。
online-invite-detail = 对方在“加入会话”界面填写地址的位置粘贴它。
online-stop = 停止互联网访问

## Joining

join-title = 选择主机
join-searching = 正在此网络中查找主机。另一位玩家需要先选择“创建会话”。如果一直没有出现，请在两台电脑的防火墙中允许 TSMP。
join-manual = 或输入主机的地址或邀请
join-manual-detail = 在同一网络中，填写主机在会话界面看到的地址；在其他网络中，填写主机发给你的邀请。
join-manual-connect = 连接
join-manual-bad = 这既不是地址也不是邀请。它们形如 192.168.0.10 和 ABCD-EFGH@play.example.org。
join-failed-title = 加入失败
join-working-title = 正在加入
join-connecting = 正在连接…
join-comparing = 正在比较模组。首次会读取整个 Mods 文件夹。
host-unnamed = 主机
review-title = 与 { $host } 的游戏保持一致
review-download =
    { $n ->
       *[other] 下载 { $n } 个文件（{ $size }）
    }
review-move =
    { $n ->
       *[other] 把主机没有的 { $n } 个文件移到 Mods.tsmp-backup
    }
review-save = 下载共享存档 { $name }（{ $size }）
review-more = 以及另外 { $n } 个
review-keep = 不会删除任何内容。被替换和移走的文件保存在 Mods 和 saves 旁边的备份文件夹中。
review-trust = 主机的脚本模组将以游戏的权限在这台电脑上运行。请只加入你信任的人的会话。
review-versions = 游戏版本不同：你的是 { $ours }，主机的是 { $theirs }。请先把两边的游戏都更新。
version-unknown = 未知
sync = 同步
syncing-title = 正在从主机复制
syncing-progress = { $done } / { $total }
ready-matched = 模组原本就一致。
ready-changed = 已下载文件：{ $fetched }。已移走文件：{ $moved }。
ready-backup = 你原来的文件在 { $path }
ready-save-downloaded = 共享存档 { $name } 已下载。
ready-save-matched = 共享存档 { $name } 原本就一致。
leave = 离开
session-packs = 本次会话的资料包
session-packs-off = 已为你关闭

## Buttons used on several screens

back = 返回
cancel = 取消

## The game

game-ready = 大家都准备好后再启动游戏。
game-starting-steam = 正在通过 Steam 启动。如果 Steam 询问是否允许启动参数，请确认。
game-starting = 正在启动游戏…
game-running = 游戏正在使用本次会话的资料包运行。
game-wrong-packs = 游戏载入了：{ $loaded }。会话需要：{ $expected }。请关闭游戏并从这里重新启动。
game-failed = 游戏未能启动：{ $message }
game-start = 启动游戏

## Multiplayer

live-no-mod = 正在等待游戏中的 TSMP 模组连接。
live-no-remote = 另一位玩家的启动器尚未连接。
live-load-save = 载入共享存档以加入会话。
live-wait-remote = 正在等待另一位玩家载入共享存档。
live-on = 已联机，两边的游戏已连接。
live-syncing = 正在让两边的游戏保持一致。请保持两边都打开；这可能需要一两分钟。
live-apart = 两边的游戏都在运行，各自在自己的地块或自己的存档里。

## Sizes and times

size-b = { $n } B
size-kb = { $n } KB
size-mb = { $n } MB
size-gb = { $n } GB
size-tb = { $n } TB
ago-now = 刚刚
ago-minutes = { $n } 分钟前
ago-hours = { $n } 小时前
ago-days =
    { $n ->
       *[other] { $n } 天前
    }

fact-building = 一起建造
building-checking = 正在检查…
building-ready = 已支持此游戏版本
building-missing = 还没有人描述游戏 { $version }，所以一起建造已关闭。其他功能正常。
building-unknown = 游戏版本未知，所以一起建造已关闭。其他功能正常。
update-ready = 版本 { $version } 已安装，重启后生效
update-restart = 重启并使用版本 { $version }

## The lobby, the journal and copying errors

copy = 复制
copied = 已复制
journal = 日志
journal-empty = 还没有任何记录。
join-failed-hint = 请检查主机是否已关闭会话，以及两台电脑是否在同一网络中。
guest-title = 你已在会话中
player-you = 你
player-host = 主机
player-you-host = 你（主机）
player-starting = 正在启动游戏
player-in-game = 游戏中
player-failed = 错误

## Two ways to play: one shared save, or everyone brings a family of their own

step-load-own = 载入自己的存档
host-mode-title = 想怎么玩？
mode-shared = 一起玩同一个家庭
mode-shared-detail = 由你选择一个存档。另一位玩家会得到一份副本，你们一起玩。
mode-own = 各玩各的家庭
mode-own-detail = 每个人在自己的游戏里创建家庭，然后都住进你的世界。
own-step-1 = 每个人像平常一样在自己的游戏里创建家庭并保存。
own-step-2 = 点击“开始创建”。然后双方都从 TSMP 启动游戏，并载入各自的存档。
own-step-3 = 另一位玩家的家庭会搬进你的世界；如果那块地皮空着，房子也一起搬来。之后你们就一起玩。
own-world = 你的存档会成为共同的世界。照常保存即可。
own-new = 这种玩法是新功能。尝试之前请先保存游戏。
mode-label = 玩法
lobby-own-host = 在游戏里载入你的存档。另一位玩家载入自己的存档后，对方的家庭会搬进你的世界。
lobby-own-guest = 在游戏里载入你自己的存档。你的家庭会搬进主机的世界，你可以在那里继续玩。
lobby-own-keep = 之后你的游戏里是主机的世界。想让自己的存档保持原样，请在游戏里选择“另存为”，不要选“保存”。
review-own = 不会复制存档。你载入自己的存档，你的家庭会搬进主机的世界。
live-load-own = 载入自己的存档以加入会话。
live-wait-remote-own = 正在等待另一位玩家载入对方的存档。
