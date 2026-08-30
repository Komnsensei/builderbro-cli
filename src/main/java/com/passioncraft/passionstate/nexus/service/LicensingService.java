package com.passioncraft.passionstate.nexus.service;

import com.passioncraft.passionstate.nexus.model.LicenseApplication;
import com.passioncraft.passionstate.nexus.model.License;
import com.passioncraft.passionstate.nexus.model.ApplicationStatus;
import com.passioncraft.passionstate.nexus.repository.LicenseRepository;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.Optional;
import java.util.UUID;

@Service
public class LicensingService {

    private final LicenseRepository licenseRepository;

    public LicensingService(LicenseRepository licenseRepository) {
        this.licenseRepository = licenseRepository;
    }

    public License applyForLicense(LicenseApplication application) {
        // Basic validation
        if (application.getApplicantId() == null || application.getApplicationType() == null) {
            throw new IllegalArgumentException("Applicant ID and application type are required.");
        }

        License newLicense = new License();
        newLicense.setLicenseId(UUID.randomUUID().toString());
        newLicense.setApplicantId(application.getApplicantId());
        newLicense.setApplicationType(application.getApplicationType());
        newLicense.setSubmissionDate(LocalDateTime.now());
        newLicense.setStatus(ApplicationStatus.PENDING);
        newLicense.setDetails(application.getDetails());

        return licenseRepository.save(newLicense);
    }

    public Optional<License> getLicenseDetails(String licenseId) {
        return licenseRepository.findById(licenseId);
    }

    public License approveLicense(String licenseId, String approverId) {
        return licenseRepository.findById(licenseId).map(license -> {
            if (license.getStatus() == ApplicationStatus.PENDING) {
                license.setStatus(ApplicationStatus.APPROVED);
                license.setApprovalDate(LocalDateTime.now());
                license.setApprovedBy(approverId);
                return licenseRepository.save(license);
            } else {
                throw new IllegalStateException("License is not in PENDING status for approval.");
            }
        }).orElseThrow(() -> new IllegalArgumentException("License not found with ID: " + licenseId));
    }

    // Other methods like rejectLicense, revokeLicense, etc. would follow.
}
