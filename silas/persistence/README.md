# persistence/

SQLite-backed storage. Memory store, subscription state, plan history, and approval records. All durable state flows through here. Stores are thin wrappers — business logic lives in core/ or the consuming module.
