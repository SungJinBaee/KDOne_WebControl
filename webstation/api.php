<?php
declare(strict_types=1);

// Native Web Station PHP on the same NAS reaches the loopback-only API.
// No KDOne or administrator credentials belong in this file.
ini_set('display_errors', '0');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');

function fail_response(int $status, string $message): void {
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['error' => $message], JSON_UNESCAPED_UNICODE);
    exit;
}

if (!extension_loaded('curl')) {
    fail_response(503, 'Web Station PHP 프로필에서 curl 확장을 활성화해 주세요.');
}

$method = $_SERVER['REQUEST_METHOD'] ?? '';
$path = $_GET['path'] ?? '';
if (!is_string($path) || strlen($path) > 2048) {
    fail_response(400, '올바르지 않은 API 경로입니다.');
}
$parts = parse_url($path);
if ($parts === false || isset($parts['scheme']) || isset($parts['host']) || isset($parts['fragment'])) {
    fail_response(400, '올바르지 않은 API 경로입니다.');
}
$route = $parts['path'] ?? '';
if (str_starts_with($route, 'external/') && !in_array(strtolower((string)($_SERVER['HTTPS'] ?? '')), ['on', '1'], true)) {
    fail_response(403, '외부 API는 HTTPS 주소로 호출하세요.');
}
$getRoutes = ['session', 'settings', 'complexes', 'state', 'schedules', 'external/state', 'external/schedules'];
$postRoutes = ['login', 'logout', 'control', 'elevator', 'schedules', 'timers', 'automations', 'external/control', 'external/timers',
    'connection/request', 'connection/certify', 'connection/disconnect',
    'connection/saved/request', 'connection/saved/restore'];
$cancel = preg_match('~\A(schedules/[a-f0-9]{32}/cancel|timers/[a-f0-9]{32}/(stop|edit)|automations/[a-f0-9]{32}/(toggle|delete|edit))\z~', $route) === 1;
$cancel = $cancel || preg_match('~\Aexternal/automations/[a-f0-9]{32}/run\z~', $route) === 1;
if (!(($method === 'GET' && in_array($route, $getRoutes, true)) ||
      ($method === 'POST' && (in_array($route, $postRoutes, true) || $cancel)))) {
    fail_response(405, '허용되지 않은 API 요청입니다.');
}
$query = [];
parse_str($parts['query'] ?? '', $query);
$allowedQuery = $route === 'state' ? ['refresh'] : ($route === 'complexes' ? ['q'] : []);
foreach ($query as $key => $value) {
    if (!in_array($key, $allowedQuery, true) || !is_string($value)) {
        fail_response(400, '허용되지 않은 요청 항목입니다.');
    }
}
$url = 'http://127.0.0.1:8088/api/' . $route;
if ($query !== []) {
    $url .= '?' . http_build_query($query, '', '&', PHP_QUERY_RFC3986);
}

$headers = ['Accept: application/json'];
foreach (['HTTP_COOKIE' => 'Cookie', 'HTTP_X_CSRF_TOKEN' => 'X-CSRF-Token', 'HTTP_X_API_KEY' => 'X-API-Key'] as $key => $name) {
    $value = $_SERVER[$key] ?? '';
    if ($value !== '') {
        if (str_contains($value, "\r") || str_contains($value, "\n")) {
            fail_response(400, '올바르지 않은 요청 헤더입니다.');
        }
        $headers[] = $name . ': ' . $value;
    }
}
$body = '';
if ($method === 'POST') {
    if (stripos($_SERVER['CONTENT_TYPE'] ?? '', 'application/json') !== 0) {
        fail_response(415, 'JSON 요청만 허용됩니다.');
    }
    $body = file_get_contents('php://input', false, null, 0, 16385);
    if ($body === false || strlen($body) > 16384) {
        fail_response(413, '요청 크기가 너무 큽니다.');
    }
    $headers[] = 'Content-Type: application/json';
}

$handle = curl_init($url);
if ($handle === false) {
    fail_response(502, 'Python API 연결을 시작하지 못했습니다.');
}
$responseHeaders = [];
curl_setopt_array($handle, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_FOLLOWLOCATION => false,
    CURLOPT_CONNECTTIMEOUT => 5,
    CURLOPT_TIMEOUT => 180,
    CURLOPT_PROXY => '',
    CURLOPT_HTTPHEADER => $headers,
    CURLOPT_HEADERFUNCTION => static function ($curl, string $line) use (&$responseHeaders): int {
        $position = strpos($line, ':');
        if ($position !== false) {
            $name = strtolower(substr($line, 0, $position));
            if (in_array($name, ['set-cookie', 'content-type', 'cache-control', 'retry-after'], true)) {
                $responseHeaders[] = trim($line);
            }
        }
        return strlen($line);
    },
]);
if ($method === 'POST') {
    curl_setopt($handle, CURLOPT_POST, true);
    curl_setopt($handle, CURLOPT_POSTFIELDS, $body);
}
$response = curl_exec($handle);
$status = (int) curl_getinfo($handle, CURLINFO_RESPONSE_CODE);
curl_close($handle);
if ($response === false || $status < 100) {
    fail_response(502, 'Python API에 연결하지 못했습니다. 컨테이너 실행 상태와 PHP의 NAS 내부 연결을 확인하세요.');
}
http_response_code($status);
foreach ($responseHeaders as $line) {
    header($line, false); // Preserve each Set-Cookie separately, including Secure/HttpOnly.
}
echo $response;
