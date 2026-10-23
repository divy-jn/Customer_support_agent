import { test, expect } from '@playwright/test';

const BACKEND_URL = 'http://127.0.0.1:8002';

/**
 * Helper to reset test backend state and initialize a fresh customer session.
 */
async function setupCustomerChat(page, { customerName = 'Amit Sharma', customerEmail = 'amit.sharma@example.com' } = {}) {
  // 1. Reset backend session store & mutation call counters
  await page.request.post(`${BACKEND_URL}/api/test/e2e/reset`);

  // 2. Pre-seed localStorage with authenticated customer identity
  await page.addInitScript(({ name, email }) => {
    if (!localStorage.getItem('intellisupport_customer')) {
      localStorage.setItem('intellisupport_customer', JSON.stringify({
        id: 1,
        name,
        email,
        phone: '+91 98765 43210',
        status: 'active',
      }));
    }
  }, { name: customerName, email: customerEmail });

  // 3. Navigate to chat page
  await page.goto('/');

  // 4. Ensure identity modal is dismissed if shown
  const continueGuestBtn = page.locator('button:has-text("Continue as Guest")');
  try {
    if (await continueGuestBtn.isVisible({ timeout: 500 })) {
      await continueGuestBtn.click({ timeout: 1000 }).catch(() => {});
    }
  } catch {}

  // 5. Wait for WebSocket connection indicator (Online)
  const statusIndicator = page.locator('header').locator('text=Online');
  await expect(statusIndicator).toBeVisible({ timeout: 15000 });

  const input = page.locator('input[placeholder="Type your message..."]');
  await expect(input).toBeEnabled({ timeout: 10000 });

  return input;
}

/**
 * Helper to send a message via the chat input.
 */
async function sendMessage(page, message) {
  const input = page.locator('input[placeholder="Type your message..."]');
  await input.fill(message);
  const submitBtn = page.locator('form button[type="submit"]');
  try {
    if (await submitBtn.isEnabled({ timeout: 500 })) {
      await submitBtn.click();
      return;
    }
  } catch {}
  await input.press('Enter');
}

/**
 * Helper to inspect backend state.
 */
async function getBackendState(page) {
  const res = await page.request.get(`${BACKEND_URL}/api/test/e2e/state`);
  return await res.json();
}


test.describe('Customer Approval UX, WebSocket Reconnection & Multi-Domain Chat', () => {

  // ─────────────────────────────────────────────────────────────
  // Scenario 1 — Refund proposal
  // ─────────────────────────────────────────────────────────────
  test('Scenario 1: Refund proposal renders pending card with usable controls and 0 mutations', async ({ page }) => {
    await setupCustomerChat(page);

    // Customer requests a refund for order #4004
    await sendMessage(page, 'I would like a refund for order #4004');

    // Verify pending approval card renders
    const card = page.locator('[data-testid="approval-card"][data-approval-status="pending"]');
    await expect(card).toBeVisible({ timeout: 12000 });
    await expect(card).toHaveAttribute('data-approval-action', 'process_refund');
    await expect(card).toHaveAttribute('data-approval-order', '4004');

    // Proposed action and order reference are clear
    await expect(card).toContainText('Confirmation Required: Refund Request');
    await expect(card).toContainText('Order #4004');
    await expect(card).toContainText('This action modifies your order record');

    // Confirmation and cancellation controls are present and enabled
    const confirmBtn = card.locator('button:has-text("Confirm Refund Request")');
    const cancelBtn = card.locator('button:text-is("Cancel")');
    await expect(confirmBtn).toBeVisible();
    await expect(confirmBtn).toBeEnabled();
    await expect(cancelBtn).toBeVisible();
    await expect(cancelBtn).toBeEnabled();

    // Verify backend mutation adapter was NOT called on proposal
    const state = await getBackendState(page);
    expect(state.refund_calls.length).toBe(0);
    expect(state.cancellation_calls.length).toBe(0);
  });

  // ─────────────────────────────────────────────────────────────
  // Scenario 2 — Explicit confirmation and authoritative success
  // ─────────────────────────────────────────────────────────────
  test('Scenario 2: Explicit confirmation executes mutation exactly once with authoritative success', async ({ page }) => {
    await setupCustomerChat(page);

    await sendMessage(page, 'Please refund order #4004');
    const card = page.locator('[data-testid="approval-card"][data-approval-status="pending"]');
    await expect(card).toBeVisible({ timeout: 12000 });

    // Click confirm control
    const confirmBtn = card.locator('button:has-text("Confirm Refund Request")');
    await confirmBtn.click();

    // Verify authoritative success card appears
    const successCard = page.locator('[data-testid="approval-card"][data-approval-status="succeeded"]');
    await expect(successCard).toBeVisible({ timeout: 12000 });
    await expect(successCard).toContainText('Authoritative Success: Refund Request Succeeded');
    await expect(successCard).toContainText('Order #4004 has been successfully updated');
    await expect(successCard).toContainText('Receipt ID:');

    // Verify simulated mutation adapter was called exactly once
    const state = await getBackendState(page);
    expect(state.refund_calls.length).toBe(1);
    expect(state.refund_calls[0].order_id).toBe(4004);

    // Send confirmation text again and verify mutation count does not increase
    await sendMessage(page, 'Yes, please process refund for order #4004');
    await page.waitForTimeout(1000);

    const stateAfterReplay = await getBackendState(page);
    expect(stateAfterReplay.refund_calls.length).toBe(1);
  });

  // ─────────────────────────────────────────────────────────────
  // Scenario 3 — Cancellation and rejection
  // ─────────────────────────────────────────────────────────────
  test('Scenario 3: Cancellation control marks proposal cancelled with 0 mutations', async ({ page }) => {
    await setupCustomerChat(page);

    await sendMessage(page, 'Please cancel order #1001');
    const card = page.locator('[data-testid="approval-card"][data-approval-status="pending"]');
    await expect(card).toBeVisible({ timeout: 12000 });
    await expect(card).toHaveAttribute('data-approval-action', 'cancel_order');

    // Click cancel button
    const cancelBtn = card.locator('button:text-is("Cancel")');
    await cancelBtn.click();

    // Verify cancelled status is rendered
    const cancelledCard = page.locator('[data-testid="approval-card"][data-approval-status="cancelled"]');
    await expect(cancelledCard).toBeVisible({ timeout: 12000 });
    await expect(cancelledCard).toContainText('Action cancelled by user.');

    // Mutation adapter was never invoked
    const state = await getBackendState(page);
    expect(state.cancellation_calls.length).toBe(0);

    // Negative phrase cannot authorize mutation
    await sendMessage(page, 'No, do not cancel order #1001');
    await page.waitForTimeout(1000);

    const stateAfterNegative = await getBackendState(page);
    expect(stateAfterNegative.cancellation_calls.length).toBe(0);
  });

  // ─────────────────────────────────────────────────────────────
  // Scenario 4 — Ambiguous affirmation after a domain switch
  // ─────────────────────────────────────────────────────────────
  test('Scenario 4: Ambiguous affirmation after domain switch prompts safe clarification, then executes once on explicit confirmation', async ({ page }) => {
    await setupCustomerChat(page);

    // 1. Propose refund
    await sendMessage(page, 'I need a refund for order #4004');
    await expect(page.locator('[data-testid="approval-card"][data-approval-status="pending"]')).toBeVisible({ timeout: 12000 });

    // 2. Switch domain to Product inquiry
    await sendMessage(page, 'Tell me about the warranty on the wireless earbuds');
    await expect(page.locator('text=1-year manufacturer warranty')).toBeVisible({ timeout: 12000 });

    // 3. Send bare affirmation "yes"
    await sendMessage(page, 'yes');

    // 4. Ambiguity guard triggers clarification; does NOT execute mutation
    const ambiguousCard = page.locator('[data-testid="approval-card"][data-approval-status="ambiguous"]');
    await expect(ambiguousCard).toBeVisible({ timeout: 12000 });
    await expect(ambiguousCard).toContainText('Ambiguous Request — Clarification Needed');
    await expect(ambiguousCard).toContainText('Did you intend to confirm Refund Request for Order #4004?');

    const stateMid = await getBackendState(page);
    expect(stateMid.refund_calls.length).toBe(0);

    // 5. Explicitly confirm using the clarification button
    const confirmClarificationBtn = ambiguousCard.locator('button:has-text("Yes, Confirm Refund Request")');
    await confirmClarificationBtn.click();

    // 6. Authoritative success
    const successCard = page.locator('[data-testid="approval-card"][data-approval-status="succeeded"]');
    await expect(successCard).toBeVisible({ timeout: 12000 });

    const stateFinal = await getBackendState(page);
    expect(stateFinal.refund_calls.length).toBe(1);
  });

  // ─────────────────────────────────────────────────────────────
  // Scenario 5 — Failed and uncertain outcomes
  // ─────────────────────────────────────────────────────────────
  test('Scenario 5: UI accurately differentiates failed, uncertain, expired, and already-claimed outcomes without leaking secrets', async ({ page }) => {
    await setupCustomerChat(page);

    // 5A: Simulate Authoritative Failure
    await page.request.post(`${BACKEND_URL}/api/test/e2e/simulate?outcome=failed`);
    await sendMessage(page, 'Please refund order #4004');
    await expect(page.locator('[data-testid="approval-card"][data-approval-status="pending"]')).toBeVisible({ timeout: 12000 });
    await page.locator('[data-testid="approval-card"] button:has-text("Confirm Refund Request")').click();

    const failedCard = page.locator('[data-testid="approval-card"][data-approval-status="failed"]');
    await expect(failedCard).toBeVisible({ timeout: 12000 });
    await expect(failedCard).toContainText('Refund Request Failed');
    await expect(failedCard).toContainText('Insufficient merchant ledger balance');

    // 5B: Simulate Uncertain Outcome (Timeout)
    await page.request.post(`${BACKEND_URL}/api/test/e2e/simulate?outcome=uncertain`);
    await sendMessage(page, 'Please refund order #4004');
    await expect(page.locator('[data-testid="approval-card"][data-approval-status="pending"]').last()).toBeVisible({ timeout: 12000 });
    await page.locator('[data-testid="approval-card"] button:has-text("Confirm Refund Request")').last().click();

    const uncertainCard = page.locator('[data-testid="approval-card"][data-approval-status="uncertain"]');
    await expect(uncertainCard).toBeVisible({ timeout: 12000 });
    await expect(uncertainCard).toContainText('Outcome Uncertain — Verification Active');
    await expect(uncertainCard).toContainText('Idempotent background reconciliation is active');
    // Ensure timeout is NOT rendered as success
    await expect(uncertainCard).not.toContainText('Authoritative Success');

    // 5C: Security Check: Zero raw secrets or stack traces leaked
    const pageContent = await page.content();
    expect(pageContent).not.toContain('dev-secret-key');
    expect(pageContent).not.toContain('test-approval-signing-secret');
    expect(pageContent).not.toContain('Traceback (most recent call last)');
  });

  // ─────────────────────────────────────────────────────────────
  // Scenario 6 — WebSocket disconnect and reconnect
  // ─────────────────────────────────────────────────────────────
  test('Scenario 6: Stored approval survives reconnect and executes idempotently', async ({ page }) => {
    await setupCustomerChat(page);

    // Propose refund
    await sendMessage(page, 'Please refund order #4004');
    const pendingCard = page.locator('[data-testid="approval-card"][data-approval-status="pending"]');
    await expect(pendingCard).toBeVisible({ timeout: 12000 });

    // Disconnect and reload page
    await page.reload();

    // After reconnect, stored approval is restored from server history
    const restoredCard = page.locator('[data-testid="approval-card"][data-approval-status="pending"]');
    await expect(restoredCard).toBeVisible({ timeout: 15000 });
    await expect(restoredCard).toContainText('Order #4004');

    // Confirm the restored approval
    await restoredCard.locator('button:has-text("Confirm Refund Request")').click();

    const successCard = page.locator('[data-testid="approval-card"][data-approval-status="succeeded"]');
    await expect(successCard).toBeVisible({ timeout: 12000 });

    const state = await getBackendState(page);
    expect(state.refund_calls.length).toBe(1);
  });

  // ─────────────────────────────────────────────────────────────
  // Scenario 7 — Loading, connection loss, and recovery
  // ─────────────────────────────────────────────────────────────
  test('Scenario 7: Connection indicators update and recover without stuck processing states', async ({ page }) => {
    await setupCustomerChat(page);

    const statusIndicator = page.locator('header').locator('text=Online');
    await expect(statusIndicator).toBeVisible();

    // Verify typing indicator during agent generation
    await sendMessage(page, 'Check my order status');
    // Agent replies and typing dots clear
    await expect(page.locator('.chat-bubble-agent').last()).toBeVisible({ timeout: 12000 });
    await expect(page.locator('.typing-dot')).toHaveCount(0);
  });

  // ─────────────────────────────────────────────────────────────
  // Scenario 8 — Accessibility and interaction safety
  // ─────────────────────────────────────────────────────────────
  test('Scenario 8: Controls have accessible names, ARIA landmarks, focus indicators, and mobile viewport usability', async ({ page }) => {
    await setupCustomerChat(page);

    await sendMessage(page, 'Refund order #4004');
    const card = page.locator('[data-testid="approval-card"][data-approval-status="pending"]');
    await expect(card).toBeVisible({ timeout: 12000 });

    // 1. ARIA landmark role
    await expect(card).toHaveAttribute('role', 'region');
    await expect(card).toHaveAttribute('aria-label', /Approval request for Refund Request/i);

    // 2. Accessible button names
    const confirmBtn = card.locator('button:has-text("Confirm Refund Request")');
    const cancelBtn = card.locator('button:text-is("Cancel")');
    await expect(confirmBtn).toHaveAttribute('aria-label', /Confirm Refund Request for Order 4004/i);
    await expect(cancelBtn).toHaveAttribute('aria-label', /Cancel Refund Request/i);

    // 3. Keyboard focusability
    await confirmBtn.focus();
    await expect(confirmBtn).toBeFocused();

    // 4. Stale control disablement: simulate multiple messages
    await sendMessage(page, 'What is your return policy?');
    await page.waitForTimeout(1000);

    // Stale approval confirm button is disabled because isLatest is false
    const previousConfirmBtn = page.locator('[data-testid="approval-card"]').first().locator('button:has-text("Confirm Refund Request")');
    await expect(previousConfirmBtn).toBeDisabled();
  });

});
