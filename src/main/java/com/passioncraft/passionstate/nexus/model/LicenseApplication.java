package com.passioncraft.passionstate.nexus.model;

import java.util.Map;

public class LicenseApplication {
    private String applicantId;
    private String applicationType;
    private Map<String, String> details; // e.g., "name": "John Doe", "dob": "1990-01-01"

    // Getters and Setters
    public String getApplicantId() { return applicantId; }
    public void setApplicantId(String applicantId) { this.applicantId = applicantId; }
    public String getApplicationType() { return applicationType; }
    public void setApplicationType(String applicationType) { this.applicationType = applicationType; }
    public Map<String, String> getDetails() { return details; }
    public void setDetails(Map<String, String> details) { this.details = details; }
}
