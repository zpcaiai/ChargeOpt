-- Public bootstrap credentials must never remain active after migrations.
-- This predicate only disables the original known seed and leaves rotated accounts intact.

UPDATE chargeopt.users
SET active = false
WHERE id = 'usr-bootstrap-admin'
  AND password_salt = 'chargeopt-demo-salt-v1'
  AND password_hash = '355c2c0e60f794ed2ef3c826351db835cd1035c0a8b679a4b738cd45e902f477';

UPDATE chargeopt.sessions
SET revoked_at = now()
WHERE user_id = 'usr-bootstrap-admin'
  AND revoked_at IS NULL
  AND EXISTS (
      SELECT 1
      FROM chargeopt.users u
      WHERE u.id = 'usr-bootstrap-admin'
        AND u.active = false
        AND u.password_salt = 'chargeopt-demo-salt-v1'
  );
