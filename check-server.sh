#!/usr/bin/env bash
# Read-only server check before installing VLESS Control.
set -u

printf '%s\n\n' 'ПРОВЕРКА СЕРВЕРА ПЕРЕД УСТАНОВКОЙ'
printf '%s\n' 'ЗАНЯТЫЕ ПОРТЫ'

ss_output=$(ss -H -ltnup 2>/dev/null || true)
if [[ -z "$ss_output" ]]; then
  printf '%s\n' '- Не удалось получить список портов. Проверьте наличие команды ss.'
else
  found=0
  declare -A seen=()
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    local_addr=$(awk '{print $5}' <<< "$line")
    port=${local_addr##*:}
    case "$port" in
      80|443|8080|8088|8443|2053|2083|2087|2096|10001|10085|51820)
        found=1
        protocol=$(awk '{print $1}' <<< "$line")
        case "$protocol" in
          tcp|tcp6) transport='tcp' ;;
          udp|udp6) transport='udp' ;;
          *) transport="$protocol" ;;
        esac
        case "$local_addr" in
          127.*|\[::1\]*) scope='только локально' ;;
          *) scope='внешний интерфейс' ;;
        esac
        lower=${line,,}
        if [[ "$lower" == *xray* ]]; then owner='Xray'
        elif [[ "$lower" == *nginx* ]]; then owner='nginx'
        elif [[ "$lower" == *apache* ]]; then owner='Apache'
        elif [[ "$lower" == *docker-proxy* ]]; then owner='Docker'
        elif [[ "$lower" == *amnezia* ]]; then owner='Amnezia'
        elif [[ "$lower" == *squid* ]]; then owner='Squid'
        elif [[ "$lower" == *wireguard* ]]; then owner='WireGuard'
        else owner='другая служба'
        fi
        key="$port/$transport/$owner/$scope"
        if [[ -z "${seen[$key]:-}" ]]; then
          seen[$key]=1
          printf -- '- %s/%s — %s, %s\n' "$port" "$transport" "$owner" "$scope"
        fi
        ;;
    esac
  done <<< "$ss_output"
  (( found )) || printf '%s\n' '- Из важных портов занятых не найдено'
fi

for port in 443 8443 2053 2083 2087 2096; do
  if ! grep -Eq "(^|[[:space:]])(0\.0\.0\.0|\*|\[::\]|[0-9a-fA-F:.]+):${port}([[:space:]]|$)" <<< "$ss_output"; then
    printf -- '- %s/tcp — свободен (кандидат для установки)\n' "$port"
  fi
done
if ! grep -Eq "(^|[[:space:]])(0\.0\.0\.0|\*|\[::\]|[0-9a-fA-F:.]+):51820([[:space:]]|$)" <<< "$ss_output"; then
  printf '%s\n' '- 51820/udp — свободен (обычно используется WireGuard/AmneziaWG)'
fi

printf '\n%s\n' 'СЛУЖБЫ VPN/XRAY'
service_output=$(systemctl list-units --type=service --all --no-legend 2>/dev/null | grep -Ei 'amnezia|xray|vpn' || true)
if [[ -n "$service_output" ]]; then
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    unit=$(awk '{print $1}' <<< "$line")
    state=$(awk '{print $3}' <<< "$line")
    printf -- '- %s — %s' "$unit" "$state"
    printf '\n'
  done <<< "$service_output"
else
  printf '%s\n' '- Службы Amnezia/Xray/VPN не найдены'
fi

printf '\n%s\n' 'DOCKER-КОНТЕЙНЕРЫ'
if command -v docker >/dev/null 2>&1; then
  docker_output=$(docker ps --format '{{.Names}} — {{.Ports}}' 2>/dev/null || true)
  [[ -n "$docker_output" ]] && printf '%s\n' "$docker_output" || printf '%s\n' '- Запущенных контейнеров нет'
else
  printf '%s\n' '- Docker не установлен'
fi

printf '\n%s\n' 'РЕКОМЕНДАЦИИ ПО ПОРТАМ'
printf '%s\n' '- 443/tcp — предпочтительный порт, если свободен'
printf '%s\n' '- 8443/tcp, 2053/tcp, 2083/tcp, 2087/tcp, 2096/tcp — альтернативы'
printf '%s\n' '- 10000–20000/tcp — запасной диапазон после проверки firewall'
printf '%s\n' '- 51820/udp — обычно WireGuard/AmneziaWG; Xray TCP с ним не конфликтует'
printf '\n%s\n' 'ВАЖНО'
printf '%s\n' '- Если найден действующий Xray или его служба, второй Xray не устанавливайте.'
printf '%s\n' '- Не останавливайте найденные службы без проверки: это может отключить пользователей.'
printf '%s\n' '- Свободный порт нужно дополнительно открыть в firewall и панели VPS.'
