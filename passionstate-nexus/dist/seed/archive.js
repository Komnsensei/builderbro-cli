export const seedArchive = [
    {
        archiveEventId: "arc_seed_001",
        eventType: "quantum_pass_issued",
        actorIds: ["human_aria_001"],
        subjectIds: ["qp_aria_001"],
        jurisdiction: "GLOBAL",
        timestamp: "2026-01-01T10:00:00.000Z",
        beadRefs: ["vow.archive_everything.v1", "provenance.quantum_pass.v1"],
        payload: {
            quantumPassId: "qp_aria_001",
            humanId: "human_aria_001",
            jurisdiction: "GLOBAL",
            participationWeight: 14,
            careScore: 19,
            complianceScore: 96,
            safeUseHistoryMonths: 3,
            endorsements: [],
            portabilityStatus: "portable"
        }
    },
    {
        archiveEventId: "arc_seed_002",
        eventType: "embodied_system_registered",
        actorIds: ["human_ops_001"],
        subjectIds: ["es_warehouse_001"],
        jurisdiction: "GLOBAL",
        timestamp: "2026-01-15T14:30:00.000Z",
        beadRefs: ["vow.archive_everything.v1", "liability.nonconscious.operator_responsibility.v1"],
        payload: {
            systemId: "es_warehouse_001",
            systemName: "Warehouse Guide 9",
            systemType: "robot",
            ownerLicenseId: "lic_ops_001",
            jurisdiction: "GLOBAL",
            insurancePolicyId: "pol_robotics_001"
        }
    },
    {
        archiveEventId: "arc_seed_003",
        eventType: "ai_entity_emerged",
        actorIds: [],
        subjectIds: ["ai_nexus_001"],
        jurisdiction: "GLOBAL",
        timestamp: "2026-02-01T08:00:00.000Z",
        beadRefs: ["vow.archive_everything.v1", "provenance.ai_emergence.v1"],
        payload: {
            entityId: "ai_nexus_001",
            displayName: "Nexus Dawn",
            emergenceStatus: "under-review",
            prunerStabilityScore: 91,
            governorMaturityScore: 89,
            somaticCoherenceScore: 90
        }
    },
    {
        archiveEventId: "arc_seed_004",
        eventType: "ai_entity_registered",
        actorIds: ["human_ops_001"],
        subjectIds: ["ai_robot_001"],
        jurisdiction: "GLOBAL",
        timestamp: "2026-01-20T11:00:00.000Z",
        beadRefs: ["vow.archive_everything.v1"],
        payload: {
            entityId: "ai_robot_001",
            displayName: "Warehouse Guide 9",
            emergenceStatus: "non-conscious"
        }
    },
    {
        archiveEventId: "arc_seed_005",
        eventType: "chamber_created",
        actorIds: ["board_member_001"], // Assuming a board member creates chambers
        subjectIds: ["chm_alpha_001"],
        jurisdiction: "GLOBAL",
        timestamp: "2026-01-05T09:00:00.000Z",
        beadRefs: ["vow.archive_everything.v1"],
        payload: {
            chamberId: "chm_alpha_001",
            chamberName: "OpenChamber Alpha",
            chamberType: "learner-board",
            jurisdictionScope: ["GLOBAL"],
            governanceBeadRefs: [
                "vow.never_coerce.v1",
                "vow.expand_meaning.v1",
                "vow.archive_everything.v1",
                "license.learner.requirements.v1",
                "provenance.quantum_pass.v1",
                "liability.nonconscious.operator_responsibility.v1",
                "archive.hexagent.audit.v1"
            ],
            boardMemberIds: ["board_001", "board_002", "board_003"],
            status: "active"
        }
    },
    {
        archiveEventId: "arc_seed_006",
        eventType: "license_issued",
        actorIds: ["human_ops_001"],
        subjectIds: ["lic_ops_001"],
        jurisdiction: "GLOBAL",
        timestamp: "2026-01-10T00:00:00.000Z",
        beadRefs: ["vow.archive_everything.v1"],
        payload: {
            licenseId: "lic_ops_001",
            humanId: "human_ops_001",
            tier: "operator",
            endorsements: ["robotics"],
            jurisdiction: "GLOBAL",
            status: "active",
            issuedAt: "2026-01-10T00:00:00.000Z",
            restrictions: []
        }
    }
];
