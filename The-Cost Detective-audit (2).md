## **The "Cost Detective" audit**

**Scenario:** You have inherited an AWS account from a previous team that was reckless with spending. Your budget is tight. You must identify waste, implement governance, and propose a savings plan.

---

### **Objectives**

- Analyze existing spend to identify "Zombie Assets"

- Implement active cost controls (Budgets and Alerts)

- Architect a "Cost-Aware" solution using Spot Instances

---

### **Instructions**

#### **1. Analysis and cleanup**

- Launch a few "wasteful" resources (e.g., unattached EBS volumes, unassociated Elastic IPs, an idle large EC2 instance) in a sandbox environment

- Use AWS Cost Explorer or Trusted Advisor to detect these — take screenshots of the findings

- Write a script (Python/Boto3 or Bash) to automatically "garbage collect" (delete) unattached EBS volumes

#### **2. Governance**

- Create an AWS Budget that alerts via SNS/Email when forecasted spend exceeds $50 (or a conceptual limit)

- Implement and document a "Tagging Policy" (using AWS Config or Service Control Policies) that prevents launching EC2 instances without a CostCenter tag

#### **3. Optimization architecture**

- Create an Auto Scaling Group that uses a Mixed Instances Policy (combining On-Demand base capacity with Spot Instances for scaling) to demonstrate cost reduction for a stateless workload

- Create an end-to-end guide on basic cost optimization in an AWS environment — this should be practical and implementable

Make sure to document everything in this audit. Submission includes documentation and a live walkthrough.
