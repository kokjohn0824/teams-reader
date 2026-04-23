#!/bin/bash
# Teams 啟動腳本：開啟 CDP debug port 供讀取訊息使用
# 使用方式：./teams_launch.sh

CDP_PORT=9229

check_cdp() {
    curl -s "http://localhost:${CDP_PORT}/json" > /dev/null 2>&1
}

if check_cdp; then
    echo "✅ Teams 已在 CDP port ${CDP_PORT} 執行中"
    exit 0
fi

echo "🔄 正在重啟 Teams（加開 CDP port ${CDP_PORT}）..."

# 關閉現有 Teams
osascript -e 'quit app "Microsoft Teams"' 2>/dev/null
sleep 3

# 帶 debug flag 啟動
WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--remote-debugging-port=${CDP_PORT}" \
    open -a "Microsoft Teams"

echo "⏳ 等待 Teams 啟動..."
for i in $(seq 1 30); do
    sleep 2
    if check_cdp; then
        echo "✅ CDP 連線就緒（${i}x2 秒）"
        exit 0
    fi
done

echo "❌ 逾時：Teams CDP port 無回應"
exit 1
