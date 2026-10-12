export class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  static reset(): void {
    FakeWebSocket.instances = [];
  }

  static latest(): FakeWebSocket {
    const socket = FakeWebSocket.instances.at(-1);
    if (socket === undefined) throw new Error("no WebSocket has been opened");
    return socket;
  }

  readonly url: string;
  binaryType: BinaryType = "blob";
  readyState = 0;
  closeCalls = 0;
  sent: (string | Uint8Array)[] = [];
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string | URL) {
    this.url = String(url);
    FakeWebSocket.instances.push(this);
  }

  send(data: string | Uint8Array): void {
    this.sent.push(data);
  }

  close(): void {
    this.closeCalls += 1;
    this.readyState = 3;
  }

  serverOpen(): void {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }

  serverSend(data: string | ArrayBuffer): void {
    this.onmessage?.(new MessageEvent("message", { data }));
  }

  serverClose(code: number): void {
    this.readyState = 3;
    this.onclose?.(new CloseEvent("close", { code }));
  }

  sentText(): string[] {
    return this.sent.filter((frame): frame is string => typeof frame === "string");
  }

  sentBytes(): number[][] {
    return this.sent
      .filter((frame): frame is Uint8Array => typeof frame !== "string")
      .map((frame) => Array.from(frame));
  }
}
