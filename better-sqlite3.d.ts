// Type shim for better-sqlite3 — full types live in extensions/bee/node_modules
// This satisfies the root tsconfig include which covers extensions/**/*
declare module 'better-sqlite3' {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  interface Database {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    prepare(sql: string): any;
    close(): void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    [key: string]: any;
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  interface DatabaseConstructor {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    new(path: string, options?: any): Database;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (path: string, options?: any): Database;
  }

  namespace Database {
    type Database = import('better-sqlite3').Database;
  }

  const Database: DatabaseConstructor;
  export = Database;
}
