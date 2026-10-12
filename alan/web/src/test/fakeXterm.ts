export interface FakeDisposable {
  dispose: () => void;
}

export class FakeFitAddon {
  static instances: FakeFitAddon[] = [];

  static latest(): FakeFitAddon {
    const addon = FakeFitAddon.instances.at(-1);
    if (addon === undefined) throw new Error("no FitAddon has been created");
    return addon;
  }

  terminal: FakeTerminal | null = null;
  fitCalls = 0;
  nextSize: { cols: number; rows: number } | null = null;

  constructor() {
    FakeFitAddon.instances.push(this);
  }

  fit(): void {
    this.fitCalls += 1;
    if (this.nextSize !== null && this.terminal !== null) {
      this.terminal.cols = this.nextSize.cols;
      this.terminal.rows = this.nextSize.rows;
    }
  }
}

export class FakeTerminal {
  static instances: FakeTerminal[] = [];

  static reset(): void {
    FakeTerminal.instances = [];
    FakeFitAddon.instances = [];
  }

  static latest(): FakeTerminal {
    const terminal = FakeTerminal.instances.at(-1);
    if (terminal === undefined) throw new Error("no Terminal has been created");
    return terminal;
  }

  readonly options: Record<string, unknown>;
  cols = 80;
  rows = 24;
  written: (string | Uint8Array)[] = [];
  host: HTMLElement | null = null;
  focusCalls = 0;
  disposed = false;
  addons: FakeFitAddon[] = [];
  keyHandler: ((event: KeyboardEvent) => boolean) | null = null;
  private dataListeners = new Set<(data: string) => void>();
  private binaryListeners = new Set<(data: string) => void>();

  constructor(options: Record<string, unknown> = {}) {
    this.options = options;
    FakeTerminal.instances.push(this);
  }

  loadAddon(addon: FakeFitAddon): void {
    addon.terminal = this;
    this.addons.push(addon);
  }

  attachCustomKeyEventHandler(handler: (event: KeyboardEvent) => boolean): void {
    this.keyHandler = handler;
  }

  open(host: HTMLElement): void {
    this.host = host;
  }

  write(chunk: string | Uint8Array): void {
    this.written.push(chunk);
  }

  focus(): void {
    this.focusCalls += 1;
  }

  dispose(): void {
    this.disposed = true;
  }

  onData(listener: (data: string) => void): FakeDisposable {
    this.dataListeners.add(listener);
    return {
      dispose: () => {
        this.dataListeners.delete(listener);
      },
    };
  }

  onBinary(listener: (data: string) => void): FakeDisposable {
    this.binaryListeners.add(listener);
    return {
      dispose: () => {
        this.binaryListeners.delete(listener);
      },
    };
  }

  typeData(data: string): void {
    for (const listener of this.dataListeners) listener(data);
  }

  typeBinary(data: string): void {
    for (const listener of this.binaryListeners) listener(data);
  }

  pressKey(event: KeyboardEvent): boolean {
    return this.keyHandler?.(event) ?? true;
  }

  writtenText(): string {
    return this.written
      .map((chunk) => (typeof chunk === "string" ? chunk : new TextDecoder().decode(chunk)))
      .join("");
  }
}
