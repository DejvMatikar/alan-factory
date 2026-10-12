// Every entry below 10080 is a browser-specification fact (Chrome's unsafe-port
// list). 5900 is not: it's a host-service fact. The e2e runner's built-in
// com.apple.screensharing daemon holds a root-owned wildcard listener on 5900
// that the unprivileged holders probe in port-avoidance.ts cannot see, so the
// walk must never hand this one port out. No neighbouring ports are added —
// the service holds none.
export const RESTRICTED_PORTS: ReadonlySet<number> = new Set([
  1, 7, 9, 11, 13, 15, 17, 19, 20, 21, 22, 23, 25, 37, 42, 43, 53, 69, 77, 79, 87, 95,
  101, 102, 103, 104, 109, 110, 111, 113, 115, 117, 119, 123, 135, 137, 139, 143, 161,
  179, 389, 427, 465, 512, 513, 514, 515, 526, 530, 531, 532, 540, 548, 554, 556, 563,
  587, 601, 636, 989, 990, 993, 995, 1719, 1720, 1723, 2049, 3659, 4045, 4190, 5060,
  5061, 5900, 6000, 6566, 6665, 6666, 6667, 6668, 6669, 6679, 6697, 10080,
]);
