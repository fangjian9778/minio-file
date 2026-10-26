#!/usr/bin/env bash
# =============================================================
# minio_cli.sh — MinIO 文件管理 Shell 脚本 (零依赖: bash+curl+openssl)
# 环境: BC Linux 8.2 / CentOS 7+ / RHEL 7+
#
# ★ 配置硬编码在下方 __CONFIG__ 区块, 直接改那几行即可 ★
#
# 用法:
#   bash minio_cli.sh                  # 交互式引导
#   bash minio_cli.sh ls -trl          # 一次性命令
#   bash minio_cli.sh get "*.bak" /tmp
#   bash minio_cli.sh put /data/backup*.sql
# =============================================================

set -u

# =============== ★★★ 硬编码配置区块 (改这里) ★★★ ===============
# MinIO API 端口 (默认 9000, 不是 console 的 9001)
MC_HOST="10.204.184.236"
MC_PORT="8109"
MC_AK="xYI5Q3x95aeWl5eLnDz9"
MC_SK="nTdgj1mH7w2tDt93NyuGilQjw3rtFDXFtZUqQLmg"
# Nginx 代理前缀 (没有就留空)
MC_PATH_PREFIX="minio-api"
# http / https
MC_PROTO="http"
# 默认桶 & 目录
DEFAULT_BUCKET="dc-ftp-transfer"
DEFAULT_PATH="downfile"
# =============== 配置区块结束 ===============

# ---------- 常量 ----------
ALGO="AWS4-HMAC-SHA256"
SERVICE="s3"
REGION="us-east-1"   # MinIO 区域不鉴权

# 运行时状态
MC_BUCKET="$DEFAULT_BUCKET"
MC_PATH="$DEFAULT_PATH"

# ---------- 敏感词脱敏 ----------
# 规则: 见用户清单 (start/restart/stop/reboot/shutdown/init/run/kill/vsftpd/java/redis/catalina/rm.log/su - root/sudo 等)
_sanitize() {
    printf '%s' "$1" | sed -E \
        -e 's/([Rr][Ee][Ss][Tt][Aa][Rr][Tt])/***/g' \
        -e 's/([Ss][Tt][Aa][Rr][Tt])/***/g' \
        -e 's/([Ss][Tt][Oo][Pp])/***/g' \
        -e 's/([Rr][Ee][Bb][Oo][Oo][Tt])/***/g' \
        -e 's/([Ss][Hh][Uu][Tt][Dd][Oo][Ww][Nn])/***/g' \
        -e 's/([Ii][Nn][Ii][Tt])/***/g' \
        -e 's/([Rr][Uu][Nn])/***/g' \
        -e 's/([Kk][Ii][Ll][Ll])/***/g' \
        -e 's/([Vv][Ss][Ff][Tt][Pp][Dd])/***/g' \
        -e 's/([Jj][Aa][Vv][Aa])/***/g' \
        -e 's/([Rr][Ee][Dd][Ii][Ss])/***/g' \
        -e 's/([Cc][Aa][Tt][Aa][Ll][Ii][Nn][Aa])/***/g' \
        -e 's/([Ss][Uu][Dd][Oo])/***/g' \
        -e 's/([Ss][Uu])[[:space:]]+-[[:space:]]+[Rr][Oo][Oo][Tt]/***/g' \
        -e 's/[Rr][Mm][[:space:]]+\.[Ll][Oo][Gg]/***/g' \
        -e 's/([Ll][Oo][Gg])/***/g' \
        -e 's/&/\*\*\*/g'
}

# ---------- 工具: HMAC / SHA256 / Hex ----------
_sha256_hex() {
    printf '%s' "$1" | openssl dgst -sha256 -hex | awk '{print $NF}'
}
_sha256_hex_file() {
    openssl dgst -sha256 -hex "$1" | awk '{print $NF}'
}
_hmac_sha256() {
    local key_hex="$1" data="$2"
    printf '%s' "$data" | openssl dgst -sha256 -mac HMAC -macopt hexkey:"$key_hex" -hex | awk '{print $NF}'
}

# ---------- SigV4 签名 key 派生链 ----------
_derive_signing_key() {
    local date_stamp="$1"
    local raw_key_hex k_date_hex k_region_hex k_service_hex k_signing_hex
    raw_key_hex=$(printf 'AWS4%s' "$MC_SK" | od -An -tx1 -v | tr -d ' \n')
    k_date_hex=$(_hmac_sha256 "$raw_key_hex" "$date_stamp")
    k_region_hex=$(_hmac_sha256 "$k_date_hex"   "$REGION")
    k_service_hex=$(_hmac_sha256 "$k_region_hex" "$SERVICE")
    k_signing_hex=$(_hmac_sha256 "$k_service_hex" "aws4_request")
    printf '%s' "$k_signing_hex"
}

# ---------- URL 编码 ----------
_url_encode() {
    local s="$1" out="" i ch code
    for (( i=0; i<${#s}; i++ )); do
        ch="${s:$i:1}"
        case "$ch" in
            [/\-_.~A-Za-z0-9]) out+="$ch" ;;
            *)  printf -v code '%02X' "'$ch"; out+="%${code}" ;;
        esac
    done
    printf '%s' "$out"
}

# ---------- 构造 S3 路径 (签名 vs curl URL 分开) ----------
# 签名用路径: 不带 Nginx 前缀, MinIO 实际收到的路径
_build_signing_path() {
    local object_key="${1:-}"
    local parts=("$MC_BUCKET")
    [[ -n "$object_key" ]] && parts+=("$object_key")
    local joined
    joined=$(IFS=/; echo "${parts[*]}")
    printf '/%s' "$joined"
}
# curl URL 路径: 带 Nginx 前缀, 让 Nginx 正确路由
_build_url_path() {
    local object_key="${1:-}"
    local parts=()
    [[ -n "$MC_PATH_PREFIX" ]] && parts+=("$MC_PATH_PREFIX")
    parts+=("$MC_BUCKET")
    [[ -n "$object_key" ]] && parts+=("$object_key")
    local joined
    joined=$(IFS=/; echo "${parts[*]}")
    printf '/%s' "$joined"
}

# ---------- Canonical Query String ----------
_build_canonical_query() {
    local -a qs=() i
    for (( i=1; i<=$#; i++ )); do qs+=("${!i}"); done
    if [[ ${#qs[@]} -eq 0 ]]; then printf ''; return; fi
    printf '%s\n' "${qs[@]}" | LC_ALL=C sort | paste -sd '&' -
}

# ---------- 简化签名: 直接 curl, 返回退出码 ----------
# 用法: _do_curl METHOD object_key body_hash [query_k=v ...] [--file local] [--out local]
# 额外 header 支持: 以 X-HDR=Value 开头的参数会被加入签名
_do_curl() {
    local method="$1" object_key="${2:-}" body_hash="$3"; shift 3
    local extra_body file_to_put=""
    local extra_headers=()
    local curl_extra=()
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --file)     shift; file_to_put="$1"; body_hash=$(_sha256_hex_file "$1") ;;
            --out)      shift; curl_extra+=(-o "$1") ;;
            --progress) curl_extra+=(--progress-bar) ;;
            *)          curl_extra+=("$1") ;;
        esac
        shift
    done

    local content_length=""
    [[ -n "$file_to_put" ]] && content_length=$(stat -c%s "$file_to_put" 2>/dev/null || stat -f%z "$file_to_put")

    local amz_date date_stamp
    amz_date=$(date -u +"%Y%m%dT%H%M%SZ")
    date_stamp=${amz_date:0:8}

    local host="${MC_HOST}:${MC_PORT}"
    local url_path canonical_query signing_path
    url_path=$(_build_url_path "$object_key")
    signing_path=$(_build_signing_path "$object_key")
    canonical_query=$(_build_canonical_query)

    local hdrs=()
    hdrs+=("host: ${host}")
    hdrs+=("x-amz-content-sha256: ${body_hash}")
    hdrs+=("x-amz-date: ${amz_date}")
    [[ -n "$content_length" ]] && hdrs+=("content-length: ${content_length}")

    local canonical_headers signed_headers
    canonical_headers=$(printf '%s\n' "${hdrs[@]}" | LC_ALL=C sort)
    canonical_headers+=$'\n'
    signed_headers=$(printf '%s\n' "${hdrs[@]%%:*}" | LC_ALL=C sort | tr '\n' ';' | sed 's/;$//')

    local canonical_request
    canonical_request="${method}"$'\n'
    canonical_request+="${signing_path}"$'\n'
    canonical_request+="${canonical_query}"$'\n'
    canonical_request+="${canonical_headers}"$'\n'
    canonical_request+="${signed_headers}"$'\n'
    canonical_request+="${body_hash}"

    local credential_scope string_to_sign
    credential_scope="${date_stamp}/${REGION}/${SERVICE}/aws4_request"
    string_to_sign="${ALGO}"$'\n'"${amz_date}"$'\n'"${credential_scope}"$'\n'
    string_to_sign+=$(_sha256_hex "$canonical_request")

    local signing_key signature
    signing_key=$(_derive_signing_key "$date_stamp")
    signature=$(_hmac_sha256 "$signing_key" "$string_to_sign")

    local auth_header="${ALGO} Credential=${MC_AK}/${credential_scope}, SignedHeaders=${signed_headers}, Signature=${signature}"
    local full_url="${MC_PROTO}://${host}${url_path}"
    [[ -n "$canonical_query" ]] && full_url+="?${canonical_query}"

    local -a final_curl=(curl -S -X "$method" "$full_url")
    for h in "${hdrs[@]}"; do final_curl+=(-H "$h"); done
    final_curl+=(-H "Authorization: ${auth_header}")
    [[ -n "$file_to_put" ]] && final_curl+=("--data-binary" "@${file_to_put}")
    final_curl+=("${curl_extra[@]}")
    "${final_curl[@]}"
}

# ---------- S3 API 调用 (ls / list 专用, 返回 body) ----------
# Usage: _s3_request <METHOD> <object_key> [query_key=value ...] [extra_curl_opts ...]
# 特殊: PUT 时传入 [--file <local_path>] 自动计算 body_hash / content-length
_s3_request() {
    local method="$1" object_key="${2:-}"; shift 2
    local -a queries=() curl_opts=()
    local file_to_put="" body_override=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            *=*)        queries+=("$1") ;;
            --file)     shift; file_to_put="$1" ;;
            *)          curl_opts+=("$1") ;;
        esac
        shift
    done

    local amz_date date_stamp body_hash content_length=""
    amz_date=$(date -u +"%Y%m%dT%H%M%SZ")
    date_stamp=${amz_date:0:8}

    if [[ -n "$file_to_put" ]]; then
        body_hash=$(_sha256_hex_file "$file_to_put")
        content_length=$(stat -c%s "$file_to_put" 2>/dev/null || stat -f%z "$file_to_put" 2>/dev/null)
        curl_opts+=("--data-binary" "@${file_to_put}")
    else
        body_hash=$(_sha256_hex "")
    fi

    local url_path canonical_query signing_path
    url_path=$(_build_url_path "$object_key")
    signing_path=$(_build_signing_path "$object_key")
    canonical_query=$(_build_canonical_query "${queries[@]+"${queries[@]}"}")

    local host="${MC_HOST}:${MC_PORT}"

    # hdrs 格式: name: value  (小写 name, 冒号后空格, 和 curl -H 一致)
    local hdrs=()
    hdrs+=("host: ${host}")
    hdrs+=("x-amz-content-sha256: ${body_hash}")
    hdrs+=("x-amz-date: ${amz_date}")
    [[ -n "$content_length" ]] && hdrs+=("content-length: ${content_length}")

    # canonical_headers: 小写排序, 每行 name:value
    local canonical_headers signed_headers
    canonical_headers=$(printf '%s\n' "${hdrs[@]}" | LC_ALL=C sort)
    canonical_headers+=$'\n'
    signed_headers=$(printf '%s\n' "${hdrs[@]%%:*}" | LC_ALL=C sort | tr '\n' ';' | sed 's/;$//')

    local canonical_request
    canonical_request="${method}"$'\n'
    canonical_request+="${signing_path}"$'\n'
    canonical_request+="${canonical_query}"$'\n'
    canonical_request+="${canonical_headers}"$'\n'
    canonical_request+="${signed_headers}"$'\n'
    canonical_request+="${body_hash}"

    local credential_scope string_to_sign
    credential_scope="${date_stamp}/${REGION}/${SERVICE}/aws4_request"
    string_to_sign="${ALGO}"$'\n'"${amz_date}"$'\n'"${credential_scope}"$'\n'
    string_to_sign+=$(_sha256_hex "$canonical_request")

    local signing_key signature
    signing_key=$(_derive_signing_key "$date_stamp")
    signature=$(_hmac_sha256 "$signing_key" "$string_to_sign")

    local auth_header="${ALGO} Credential=${MC_AK}/${credential_scope}, SignedHeaders=${signed_headers}, Signature=${signature}"
    local full_url="${MC_PROTO}://${host}${url_path}"
    [[ -n "$canonical_query" ]] && full_url+="?${canonical_query}"

    # final_curl: 从 hdrs 数组生成 -H, 保证和 canonical_headers 完全一致
    local -a final_curl=(curl -sS -X "$method" "$full_url")
    for h in "${hdrs[@]}"; do final_curl+=(-H "$h"); done
    final_curl+=(-H "Authorization: ${auth_header}")
    [[ ${#curl_opts[@]} -gt 0 ]] && final_curl+=("${curl_opts[@]}")

    "${final_curl[@]}"
}

# ---------- 连接测试 ----------
_test_connection() {
    local out
    out=$(_s3_request "GET" "" "list-type=2" "max-keys=1" 2>&1)
    if printf '%s' "$out" | grep -qi '<Error>\|<Message>'; then
        echo "  ✗ 连接失败" >&2
        printf '%s' "$out" | head -5 >&2
        return 1
    fi
    if [[ -n "$out" ]]; then
        echo "  ✓ 连接成功"
        return 0
    fi
    echo "  ? 无响应, 检查地址/网络" >&2
    return 1
}

# ---------- S3 ListObjectsV2 → 解析 ----------
_parse_list_v2() {
    python3 -c "
import sys, xml.etree.ElementTree as ET
NS = '{http://s3.amazonaws.com/doc/2006-03-01/}'
tree = ET.parse(sys.stdin); root = tree.getroot()
for c in root.iter(NS + 'Contents'):
    key_el = c.find(NS + 'Key'); key = key_el.text if key_el is not None else ''
    if not key or key.endswith('/'): continue
    size_el = c.find(NS + 'Size'); size = int(size_el.text) if size_el is not None else 0
    lm_el = c.find(NS + 'LastModified'); lm = lm_el.text if lm_el is not None else ''
    sys.stdout.write('%d\t%s\t%s\n' % (size, lm, key))
"
}

# ---------- ls ----------
_cmd_ls() {
    local long_fmt=false time_sort=false reverse=false
    local dir="${MC_PATH}"
    local arg
    for arg in "$@"; do
        case "$arg" in
            -*)
                [[ "$arg" == *l* ]] && long_fmt=true
                [[ "$arg" == *t* ]] && time_sort=true
                [[ "$arg" == *r* ]] && reverse=true
                ;;
            *)  dir="$arg" ;;
        esac
    done

    local list_prefix="${dir%/}/"
    [[ "$list_prefix" == "/" || -z "$list_prefix" ]] && list_prefix=""

    local s3_out parsed
    s3_out=$(_s3_request "GET" "" \
        "list-type=2" "prefix=$(_url_encode "$list_prefix")" "delimiter=/")
    parsed=$(printf '%s' "$s3_out" | _parse_list_v2)

    if [[ -z "$parsed" ]]; then
        if printf '%s' "$s3_out" | grep -qi 'error'; then
            echo "ls: 连接/权限错误" >&2
        else
            echo "(空目录)"
        fi
        return 0
    fi

    local sorted="$parsed"
    if $time_sort; then
        sorted=$(printf '%s' "$parsed" | sort -t$'\t' -k2,2r)
    else
        sorted=$(printf '%s' "$parsed" | sort -t$'\t' -k3,3)
    fi
    $reverse && sorted=$(printf '%s' "$sorted" | tac 2>/dev/null || \
        printf '%s' "$sorted" | awk '{a[NR]=$0} END{for(i=NR;i>=1;i--) print a[i]}')

    local human_size fname disp_name
    while IFS=$'\t' read -r size lm key; do
        fname="${key##*/}"
        disp_name=$(_sanitize "$fname")
        if $long_fmt; then
            human_size=$(_human_size "$size")
            local lm_short="${lm:0:19}"
            printf '%10s  %s  %s\n' "$human_size" "$lm_short" "$disp_name"
        else
            printf '%s\n' "$disp_name"
        fi
    done <<< "$sorted"
}

_human_size() {
    local bytes="$1"
    if [[ "$bytes" -ge $((1024**3)) ]]; then
        awk -v b="$bytes" 'BEGIN{printf "%.1f GB", b/1024/1024/1024}'
    elif [[ "$bytes" -ge $((1024**2)) ]]; then
        awk -v b="$bytes" 'BEGIN{printf "%.1f MB", b/1024/1024}'
    elif [[ "$bytes" -ge 1024 ]]; then
        awk -v b="$bytes" 'BEGIN{printf "%.1f KB", b/1024}'
    else
        printf '%d B' "$bytes"
    fi
}

# ---------- fnmatch ----------
_fnmatch() { local pattern="$1" str="$2"; [[ "$str" == $pattern ]]; }

# ---------- list current dir files → 匹配 pattern ----------
_collect_matched_keys() {
    local pattern="$1"
    local list_prefix="${MC_PATH%/}/"
    [[ "$list_prefix" == "/" || -z "$list_prefix" ]] && list_prefix=""
    local s3_out parsed
    s3_out=$(_s3_request "GET" "" \
        "list-type=2" "prefix=$(_url_encode "$list_prefix")" "delimiter=/")
    parsed=$(printf '%s' "$s3_out" | _parse_list_v2)

    if [[ -z "$parsed" ]]; then
        return 1
    fi

    local matched=""
    local count=0
    while IFS=$'\t' read -r size lm key; do
        local fname="${key##*/}"
        if _fnmatch "$pattern" "$fname"; then
            matched+="${fname}"$'\n'
            count=$((count + 1))
        fi
    done <<< "$parsed"

    if [[ $count -eq 0 ]]; then return 1; fi
    printf '%s' "$matched"
    return 0
}

# ---------- get (实时进度) ----------
_cmd_get() {
    local pattern="$1" local_dir="${2:-.}"

    local matched
    matched=$(_collect_matched_keys "$pattern") || {
        echo "get: 没有匹配 '$pattern' 的文件" >&2; return 1
    }

    local count
    count=$(printf '%s' "$matched" | grep -c .)
    echo "  匹配到 $count 个文件, 下载到: $local_dir"
    echo

    mkdir -p "$local_dir"
    local total_bytes=0 total_ok=0 total_fail=0
    local body_hash
    body_hash=$(_sha256_hex "")

    local fname disp_name local_path object_key file_bytes
    while IFS= read -r fname; do
        [[ -z "$fname" ]] && continue
        disp_name=$(_sanitize "$fname")
        local_path="${local_dir}/${fname}"
        object_key="${MC_PATH:+${MC_PATH}/}${fname}"

        # HEAD 拿 content-length (-X HEAD 就够, -D - 把 header dump 到 stdout)
        local head_out
        head_out=$(_do_curl "HEAD" "$object_key" "$body_hash" -D - 2>/dev/null)
        file_bytes=$(printf '%s' "$head_out" | grep -i '^Content-Length:' | awk -F: '{print $2}' | tr -d ' \r\n')
        file_bytes=${file_bytes:-0}
        local file_hsize=$(_human_size "$file_bytes")

        printf "  ↓ %s  (%s)\n" "${disp_name:0:42}" "$file_hsize"

        # GET: -w 输出到 stdout (用 $() 抓), 进度条是 stderr (透传到终端)
        local w_out curl_exit=0
        w_out=$(_do_curl "GET" "$object_key" "$body_hash" \
            -o "$local_path" \
            --progress-bar \
            --connect-timeout 30 \
            -w 'CODE=%{http_code}\nSIZE=%{size_download}\nTIME=%{time_total}\nSPEED=%{speed_download}\n' \
            2> >(cat >&2)) || curl_exit=$?

        local code dtime dspeed
        code=$(printf '%s' "$w_out" | grep '^CODE=' | cut -d= -f2)
        dtime=$(printf '%s' "$w_out" | grep '^TIME=' | cut -d= -f2)
        dspeed=$(printf '%s' "$w_out" | grep '^SPEED=' | cut -d= -f2)

        if [[ $curl_exit -eq 0 && "${code:-0}" == "200" ]]; then
            local speed_h
            speed_h=$(awk -v s="${dspeed:-0}" 'BEGIN{printf "%.1f", s/1024/1024}')
            printf "  \033[32m✓\033[0m %s  完成 (%s MB/s, %.1fs)\n\n" \
                "${disp_name:0:42}" "$speed_h" "${dtime:-0}"
            total_bytes=$((total_bytes + file_bytes))
            total_ok=$((total_ok + 1))
        else
            printf "  \033[31m✗\033[0m %s 失败 (curl=%s http=%s)\n\n" \
                "$disp_name" "$curl_exit" "${code:-n/a}"
            rm -f "$local_path"; total_fail=$((total_fail + 1))
        fi
    done <<< "$matched"

    echo "  下载完成: \033[32m✅ %d\033[0m / \033[31m❌ %d\033[0m  总计 %s" \
        "$total_ok" "$total_fail" "$(_human_size "$total_bytes")"
}

# ---------- put (上传, 实时进度) ----------
_cmd_put() {
    local pattern="$1"

    local -a local_files=()
    for f in $pattern; do
        if [[ -f "$f" ]]; then
            local_files+=("$f")
        elif [[ -f "$pattern" ]]; then
            local_files+=("$pattern"); break
        else
            echo "put: 找不到本地文件: $f" >&2
        fi
    done

    if [[ ${#local_files[@]} -eq 0 ]]; then
        echo "put: 没有匹配 '$pattern' 的本地文件" >&2; return 1
    fi

    local count=${#local_files[@]}
    echo "  待上传 $count 个文件"
    echo

    local total_bytes=0 total_ok=0 total_fail=0

    local local_file fname object_key disp_name
    for (( i=0; i<${#local_files[@]}; i++ )); do
        local_file="${local_files[$i]}"
        fname="${local_file##*/}"
        object_key="${MC_PATH:+${MC_PATH}/}${fname}"
        disp_name=$(_sanitize "$fname")
        local file_bytes
        file_bytes=$(stat -c%s "$local_file" 2>/dev/null || stat -f%z "$local_file")
        local file_hsize=$(_human_size "$file_bytes")
        total_bytes=$((total_bytes + file_bytes))

        printf "  ↑ %s  (%s)\n" "${disp_name:0:42}" "$file_hsize"

        local w_out curl_exit=0
        w_out=$(_do_curl "PUT" "$object_key" "" --file "$local_file" \
            --progress-bar \
            --connect-timeout 60 \
            -w 'CODE=%{http_code}\nSIZE=%{size_upload}\nTIME=%{time_total}\nSPEED=%{speed_upload}\n' \
            2> >(cat >&2)) || curl_exit=$?

        local code utime uspeed
        code=$(printf '%s' "$w_out" | grep '^CODE=' | cut -d= -f2)
        utime=$(printf '%s' "$w_out" | grep '^TIME=' | cut -d= -f2)
        uspeed=$(printf '%s' "$w_out" | grep '^SPEED=' | cut -d= -f2)

        if [[ $curl_exit -eq 0 && "${code:-0}" == "200" ]]; then
            local speed_h
            speed_h=$(awk -v s="${uspeed:-0}" 'BEGIN{printf "%.1f", s/1024/1024}')
            printf "  \033[32m✓\033[0m %s  完成 (%s MB/s, %.1fs)\n\n" \
                "${disp_name:0:42}" "$speed_h" "${utime:-0}"
            total_ok=$((total_ok + 1))
        else
            printf "  \033[31m✗\033[0m %s 上传失败 (curl=%s http=%s)\n\n" \
                "$disp_name" "$curl_exit" "${code:-n/a}"
            total_fail=$((total_fail + 1))
        fi
    done

    echo "  上传完成: \033[32m✅ %d\033[0m / \033[31m❌ %d\033[0m  总计 %s" \
        "$total_ok" "$total_fail" "$(_human_size "$total_bytes")"
}

# ---------- cd / pwd (内部保留, 界面禁止输出路径) ----------
_cmd_cd() { :; }
_cmd_pwd() { printf '★\n'; }

# ---------- 引导式菜单 ----------
_BANNER() {
    printf '\033[1;36m%s\033[0m\n' "$1"
}
_STEP()    { printf '\033[1;33m  [%s]\033[0m %s\n' "$1" "$2"; }
_OK()      { printf '  \033[32m✓\033[0m %s\n' "$1"; }
_INFO()    { printf '  \033[90m%s\033[0m\n' "$1"; }
_INPUT()   { printf '\033[1;34m> %s\033[0m ' "$1"; }

_run_guided() {
    _BANNER "============================================"
    _BANNER "  MINIO 文件管理 Shell CLI"
    _BANNER "============================================"
    echo

    # 第1步: 连接
    _STEP "1/3" "连接 MinIO ..."
    if ! _test_connection; then
        echo; echo "连接失败, 请先修正脚本内配置"
        return 1
    fi

    # 第2步: 列表
    echo
    _STEP "2/3" "文件列表"
    _cmd_ls -l

    # 第3步: 操作
    echo
    _STEP "3/3" "选择操作"
    cat <<MENU
  ┌─────────────────────────────────────┐
  │  1) 下载 (get)                      │
  │  2) 上传 (put)                      │
  │  3) 查看列表 (ls)                   │
  │  4) 退出                             │
  └─────────────────────────────────────┘
MENU
    while true; do
        _INPUT "选择 [1-4]"
        read -r choice
        case "$choice" in
            1)  echo; _do_download_wizard; break ;;
            2)  echo; _do_upload_wizard; break ;;
            3)  echo; _cmd_ls -l; break ;;
            4|q|quit|exit) echo; _OK "再见"; return 0 ;;
            *)  echo "  请输入 1-4" ;;
        esac
    done
}

_do_download_wizard() {
    local pattern local_dir
    _INPUT "下载匹配 (如 *.bak, 回车=全部)"
    read -r pattern
    [[ -z "$pattern" ]] && pattern="*"

    _INPUT "保存本地目录 (默认 $(pwd))"
    read -r local_dir
    [[ -z "$local_dir" ]] && local_dir="."

    echo
    _cmd_get "$pattern" "$local_dir"
}

_do_upload_wizard() {
    local pattern
    _INPUT "上传本地文件/通配符 (如 /data/*.sql)"
    read -r pattern
    [[ -z "$pattern" ]] && { echo "  未输入, 取消"; return; }

    echo
    _cmd_put "$pattern"
}

# ---------- 一次性命令模式 ----------
_run_oneoff() {
    local cmd="$1"; shift
    case "$cmd" in
        ls)   _cmd_ls "$@" ;;
        get)  _cmd_get "$@" ;;
        put)  _cmd_put "$@" ;;
        help|-h|--help) _usage ;;
        *) echo "未知命令: $cmd"; _usage; exit 1 ;;
    esac
}

_usage() {
    cat <<EOF
MinIO 文件管理 Shell CLI (零依赖: bash+curl+openssl)

用法:
  bash minio_cli.sh                       引导式菜单
  bash minio_cli.sh ls [-l] [-trl]        列文件
  bash minio_cli.sh get <pattern> [dir]   下载 (支持通配符)
  bash minio_cli.sh put <file|pattern>    上传

示例:
  bash minio_cli.sh
  bash minio_cli.sh ls -trl
  bash minio_cli.sh put /var/tmp/app.bak
  bash minio_cli.sh get "backup*.sql" /data/restore
EOF
}

# ---------- 入口 ----------
main() {
    if [[ $# -gt 0 ]]; then
        _run_oneoff "$@"
    else
        _run_guided
    fi
}
main "$@"
