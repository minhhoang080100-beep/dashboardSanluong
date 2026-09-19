const PERMISSIONS = new Set(['create', 'approve']);

function hasPlanPermission(user, permission) {
  if (!user || !['admin', 'manager'].includes(user.role) || user.is_active === false || user.must_change_password === true) return false;
  // Older APIs did not return this property. An explicit empty array is a
  // restriction and must never fall back to the legacy role permissions.
  if (!Object.hasOwn(user, 'plan_permissions')) return true;
  const values = user.plan_permissions;
  if (!Array.isArray(values) || values.length !== new Set(values).size || values.some((value) => !PERMISSIONS.has(value))) return false;
  return values.includes(permission);
}

export function canCreatePlan(user) {
  return hasPlanPermission(user, 'create');
}

export function canApprovePlan(user) {
  return hasPlanPermission(user, 'approve');
}

export function canDeletePlan(user, plan) {
  if (!plan || plan.is_deleted || !['draft', 'approved', 'cancelled'].includes(plan.status)) return false;
  const permitted = plan.status === 'approved' ? canApprovePlan(user) : canCreatePlan(user);
  if (!permitted) return false;
  return !Object.hasOwn(plan, 'can_delete') || plan.can_delete === true;
}
