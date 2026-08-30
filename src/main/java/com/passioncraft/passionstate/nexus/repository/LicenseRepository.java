package com.passioncraft.passionstate.nexus.repository;

import com.passioncraft.passionstate.nexus.model.License;
import java.util.Optional;

// This would typically extend JpaRepository or similar for Spring Data JPA
// For unit testing, we can mock this interface.
public interface LicenseRepository {
    License save(License license);
    Optional<License> findById(String id);
    // Add other repository methods as needed
}
