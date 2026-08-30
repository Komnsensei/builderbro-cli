package com.passioncraft.passionstate.nexus.service;

import com.passioncraft.passionstate.nexus.model.ApplicationStatus;
import com.passioncraft.passionstate.nexus.model.License;
import com.passioncraft.passionstate.nexus.model.LicenseApplication;
import com.passioncraft.passionstate.nexus.repository.LicenseRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.HashMap;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("LicensingService Unit Tests")
class LicensingServiceTest {

    @Mock
    private LicenseRepository licenseRepository;

    @InjectMocks
    private LicensingService licensingService;

    private LicenseApplication testApplication;
    private License testLicense;

    @BeforeEach
    void setUp() {
        testApplication = new LicenseApplication();
        testApplication.setApplicantId("user-123");
        testApplication.setApplicationType("Driver's License");
        testApplication.setDetails(new HashMap<>() {{
            put("name", "John Doe");
            put("dob", "1990-01-01");
        }});

        testLicense = new License();
        testLicense.setLicenseId("license-abc");
        testLicense.setApplicantId("user-123");
        testLicense.setApplicationType("Driver's License");
        testLicense.setStatus(ApplicationStatus.PENDING);
        testLicense.setDetails(testApplication.getDetails());
    }

    @Test
    @DisplayName("should successfully apply for a license")
    void testApplyForLicense_Success() {
        when(licenseRepository.save(any(License.class))).thenReturn(testLicense);

        License createdLicense = licensingService.applyForLicense(testApplication);

        assertNotNull(createdLicense);
        assertEquals("user-123", createdLicense.getApplicantId());
        assertEquals("Driver's License", createdLicense.getApplicationType());
        assertEquals(ApplicationStatus.PENDING, createdLicense.getStatus());
        assertNotNull(createdLicense.getLicenseId()); // Should be generated
        assertNotNull(createdLicense.getSubmissionDate());
        verify(licenseRepository, times(1)).save(any(License.class));
    }

    @Test
    @DisplayName("should throw IllegalArgumentException when applicantId is null during application")
    void testApplyForLicense_ApplicantIdNull() {
        testApplication.setApplicantId(null);
        IllegalArgumentException thrown = assertThrows(IllegalArgumentException.class, () -> {
            licensingService.applyForLicense(testApplication);
        });
        assertTrue(thrown.getMessage().contains("Applicant ID and application type are required."));
        verify(licenseRepository, never()).save(any(License.class));
    }

    @Test
    @DisplayName("should retrieve license details by ID")
    void testGetLicenseDetails_Success() {
        when(licenseRepository.findById("license-abc")).thenReturn(Optional.of(testLicense));

        Optional<License> foundLicense = licensingService.getLicenseDetails("license-abc");

        assertTrue(foundLicense.isPresent());
        assertEquals("license-abc", foundLicense.get().getLicenseId());
        verify(licenseRepository, times(1)).findById("license-abc");
    }

    @Test
    @DisplayName("should return empty optional if license not found")
    void testGetLicenseDetails_NotFound() {
        when(licenseRepository.findById("non-existent")).thenReturn(Optional.empty());

        Optional<License> foundLicense = licensingService.getLicenseDetails("non-existent");

        assertFalse(foundLicense.isPresent());
        verify(licenseRepository, times(1)).findById("non-existent");
    }

    @Test
    @DisplayName("should approve a pending license")
    void testApproveLicense_Success() {
        License pendingLicense = new License();
        pendingLicense.setLicenseId("license-to-approve");
        pendingLicense.setStatus(ApplicationStatus.PENDING);
        pendingLicense.setApplicantId("user-456");
        pendingLicense.setApplicationType("Business Permit");

        when(licenseRepository.findById("license-to-approve")).thenReturn(Optional.of(pendingLicense));
        when(licenseRepository.save(any(License.class))).thenAnswer(invocation -> invocation.getArgument(0)); // Return the modified license

        License approvedLicense = licensingService.approveLicense("license-to-approve", "admin-001");

        assertNotNull(approvedLicense);
        assertEquals(ApplicationStatus.APPROVED, approvedLicense.getStatus());
        assertEquals("admin-001", approvedLicense.getApprovedBy());
        assertNotNull(approvedLicense.getApprovalDate());
        verify(licenseRepository, times(1)).findById("license-to-approve");
        verify(licenseRepository, times(1)).save(approvedLicense);
    }

    @Test
    @DisplayName("should throw IllegalStateException if approving non-pending license")
    void testApproveLicense_NotPending() {
        License approvedAlreadyLicense = new License();
        approvedAlreadyLicense.setLicenseId("license-approved");
        approvedAlreadyLicense.setStatus(ApplicationStatus.APPROVED); // Already approved

        when(licenseRepository.findById("license-approved")).thenReturn(Optional.of(approvedAlreadyLicense));

        IllegalStateException thrown = assertThrows(IllegalStateException.class, () -> {
            licensingService.approveLicense("license-approved", "admin-001");
        });

        assertTrue(thrown.getMessage().contains("License is not in PENDING status for approval."));
        verify(licenseRepository, times(1)).findById("license-approved");
        verify(licenseRepository, never()).save(any(License.class));
    }

    @Test
    @DisplayName("should throw IllegalArgumentException if license not found for approval")
    void testApproveLicense_NotFound() {
        when(licenseRepository.findById("non-existent-license")).thenReturn(Optional.empty());

        IllegalArgumentException thrown = assertThrows(IllegalArgumentException.class, () -> {
            licensingService.approveLicense("non-existent-license", "admin-001");
        });

        assertTrue(thrown.getMessage().contains("License not found with ID: non-existent-license"));
        verify(licenseRepository, times(1)).findById("non-existent-license");
        verify(licenseRepository, never()).save(any(License.class));
    }
}
