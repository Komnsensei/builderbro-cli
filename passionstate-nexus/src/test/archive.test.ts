import { describe, it, expect } from "vitest";
import { ArchiveService } from "../core/archiveService.js";

describe("archive service", () => {
  it("creates immutable-style event records with payload hash", () => {
    const archive = new ArchiveService();

    const event = archive.record({
      eventType: "test_event",
      actorIds: ["actor_1"],
      subjectIds: ["subject_1"],
      jurisdiction: "GLOBAL",
      beadRefs: ["vow.archive_everything.v1"],
      payload: { hello: "world" }
    });

    expect(event.archiveEventId).toMatch(/^arc_/);
    expect(event.payloadHash.length).toBeGreaterThan(10);
  });

  it("queries archived events by event type", () => {
    const archive = new ArchiveService();

    archive.record({
      eventType: "find_me",
      actorIds: ["actor_2"],
      subjectIds: ["subject_2"],
      jurisdiction: "GLOBAL",
      beadRefs: ["vow.archive_everything.v1"],
      payload: { ok: true }
    });

    const results = archive.query({ eventType: "find_me" });
    expect(results.total).toBe(1);
  });
});