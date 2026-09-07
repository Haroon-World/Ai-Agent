from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List
from models import db, Business, SubscriptionRequest

PLAN_DISPLAY_NAMES = {
    "30": "Standard Monthly Plan (30 Days)",
    "90": "Quarterly Plan (90 Days)",
    "180": "Semi-Annual Plan (180 Days)",
    "365": "Annual Enterprise License (365 Days)",
    "trial_30": "1-Month Free Trial",
    "1_month": "Standard Monthly Plan (30 Days)",
    "3_months": "Quarterly Plan (90 Days)",
    "6_months": "Semi-Annual Plan (180 Days)",
    "1_year": "Annual Enterprise License (365 Days)"
}

class SubscriptionService:
    """
    Central service managing clinic client subscription lifecycle:
    - 1-Month Free Trial assignment and tracking
    - Access authorization enforcement
    - Subscription request workflow (pending approval queue)
    - Platform approvals, rejections, custom date ranges, and cancellation/reactivation
    """

    @staticmethod
    def get_subscription_info(business_id: int) -> Dict[str, Any]:
        """Fetch subscription metrics, days remaining, badge, and pending request."""
        business = db.session.get(Business, business_id)
        if not business:
            return {
                "success": False,
                "error": f"Clinic with ID {business_id} not found."
            }

        badge = business.subscription_badge
        pending = business.current_pending_request

        # Retrieve latest approved request
        latest_approved = SubscriptionRequest.query.filter_by(
            business_id=business_id, status="approved"
        ).order_by(SubscriptionRequest.reviewed_at.desc(), SubscriptionRequest.id.desc()).first()

        # Retrieve latest rejected request to alert the clinic admin if applicable
        latest_rejected = SubscriptionRequest.query.filter_by(
            business_id=business_id, status="rejected"
        ).order_by(SubscriptionRequest.reviewed_at.desc(), SubscriptionRequest.id.desc()).first()

        # Once client new plan is approved by onboarding team, wipe out rejection messages:
        if latest_rejected and latest_approved:
            app_time = latest_approved.reviewed_at or latest_approved.created_at
            rej_time = latest_rejected.reviewed_at or latest_rejected.created_at
            if (app_time and rej_time and app_time >= rej_time) or (latest_approved.id > latest_rejected.id):
                latest_rejected = None

        if business.is_subscription_valid and business.subscription_status == "active" and latest_approved:
            latest_rejected = None

        # Retrieve latest cancellation notes if subscription is cancelled
        latest_cancelled = SubscriptionRequest.query.filter_by(
            business_id=business_id, status="cancelled"
        ).order_by(SubscriptionRequest.reviewed_at.desc(), SubscriptionRequest.id.desc()).first()

        cancellation_reason = None
        if business.subscription_status == "cancelled":
            cancellation_reason = (latest_cancelled.notes if latest_cancelled and latest_cancelled.notes else "Suspended by platform administration")

        return {
            "success": True,
            "business_id": business.id,
            "clinic_name": business.name,
            "status": business.subscription_status,
            "active_plan_name": business.active_plan_name or "1-Month Free Trial",
            "is_valid": business.is_subscription_valid,
            "days_remaining": business.days_remaining,
            "subscription_start_date": business.subscription_start_date.strftime("%Y-%m-%d") if business.subscription_start_date else None,
            "trial_ends_at": business.trial_ends_at.strftime("%Y-%m-%d") if business.trial_ends_at else None,
            "subscription_expires_at": business.subscription_expires_at.strftime("%Y-%m-%d") if business.subscription_expires_at else None,
            "effective_expiry_date": business.effective_expiry_date.strftime("%Y-%m-%d") if business.effective_expiry_date else None,
            "badge": badge,
            "pending_request": pending.to_dict() if pending else None,
            "latest_rejected_request": latest_rejected.to_dict() if latest_rejected else None,
            "is_cancelled": (business.subscription_status == "cancelled"),
            "cancellation_reason": cancellation_reason
        }

    @staticmethod
    def check_access(business_id: int) -> bool:
        """Return True if the clinic owner/staff is authorized to access the clinic portal."""
        business = db.session.get(Business, business_id)
        if not business:
            return False
        return business.is_subscription_valid

    @staticmethod
    def create_subscription_request(
        business_id: int,
        duration_days: int = 30,
        plan_name: Optional[str] = None,
        requested_by: Optional[str] = "Admin",
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Record a client-initiated subscription request for platform onboarding review.
        Does NOT immediately extend days; keeps state pending until platform approval.
        """
        business = db.session.get(Business, business_id)
        if not business:
            return {"success": False, "error": f"Clinic #{business_id} not found."}

        # Check if already has an open pending request
        existing_pending = SubscriptionRequest.query.filter_by(
            business_id=business_id, status="pending"
        ).first()

        str_days = str(duration_days)
        disp_name = PLAN_DISPLAY_NAMES.get(plan_name or str_days) or PLAN_DISPLAY_NAMES.get(str_days, f"Custom Plan ({duration_days} Days)")
        norm_plan_name = plan_name or f"plan_{duration_days}d"

        if existing_pending:
            # Update existing pending request instead of duplicating
            existing_pending.duration_days = duration_days
            existing_pending.plan_name = norm_plan_name
            existing_pending.plan_display_name = disp_name
            existing_pending.requested_by_user = requested_by
            existing_pending.notes = notes
            existing_pending.created_at = datetime.now(timezone.utc)
            db.session.commit()
            return {
                "success": True,
                "message": f"Updated your pending subscription request for {disp_name}.",
                "request": existing_pending.to_dict()
            }

        req = SubscriptionRequest(
            business_id=business_id,
            plan_name=norm_plan_name,
            plan_display_name=disp_name,
            duration_days=duration_days,
            requested_by_user=requested_by,
            status="pending",
            notes=notes
        )
        db.session.add(req)
        db.session.commit()

        return {
            "success": True,
            "message": f"Subscription request for {disp_name} submitted successfully.",
            "request": req.to_dict()
        }

    @staticmethod
    def approve_subscription_request(request_id: int, reviewer_username: str = "Platform Admin") -> Dict[str, Any]:
        """
        Platform owner approves a pending subscription request.
        Activates the plan, extends the expiry date, and updates the business record.
        """
        req = db.session.get(SubscriptionRequest, request_id)
        if not req:
            return {"success": False, "error": f"Subscription request #{request_id} not found."}

        if req.status != "pending":
            return {"success": False, "error": f"Request #{request_id} is already {req.status}."}

        business = db.session.get(Business, req.business_id)
        if not business:
            return {"success": False, "error": f"Clinic #{req.business_id} not found."}

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

        # Base expiry date calculation:
        if business.subscription_status == "active" and business.subscription_expires_at:
            exp = business.subscription_expires_at.replace(tzinfo=None) if business.subscription_expires_at.tzinfo else business.subscription_expires_at
            base_date = max(now_utc, exp)
        else:
            base_date = now_utc

        business.subscription_status = "active"
        business.active_plan_name = req.plan_display_name
        business.subscription_start_date = business.subscription_start_date or now_utc
        business.subscription_expires_at = base_date + timedelta(days=req.duration_days)

        req.status = "approved"
        req.reviewed_at = datetime.now(timezone.utc)
        req.reviewed_by = reviewer_username

        # Wipe out any previous rejected or cancelled subscription notices for this clinic
        prior_notices = SubscriptionRequest.query.filter(
            SubscriptionRequest.business_id == business.id,
            SubscriptionRequest.id != req.id,
            SubscriptionRequest.status.in_(["rejected", "cancelled"])
        ).all()
        for pn in prior_notices:
            pn.status = "archived"

        db.session.commit()

        return {
            "success": True,
            "message": f"Approved request #{req.id}. Clinic '{business.name}' activated on {req.plan_display_name}.",
            "business_id": business.id,
            "subscription_expires_at": business.subscription_expires_at.strftime("%Y-%m-%d"),
            "days_remaining": business.days_remaining
        }

    @staticmethod
    def reject_subscription_request(
        request_id: int,
        reviewer_username: str = "Platform Admin",
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """Platform owner rejects a pending subscription request."""
        req = db.session.get(SubscriptionRequest, request_id)
        if not req:
            return {"success": False, "error": f"Subscription request #{request_id} not found."}

        if req.status != "pending":
            return {"success": False, "error": f"Request #{request_id} is already {req.status}."}

        req.status = "rejected"
        req.reviewed_at = datetime.now(timezone.utc)
        req.reviewed_by = reviewer_username
        if reason:
            req.notes = f"{req.notes or ''} [Rejection Reason: {reason}]".strip()

        db.session.commit()
        return {"success": True, "message": f"Subscription request #{request_id} rejected."}

    @staticmethod
    def cancel_subscription(business_id: int, reason: Optional[str] = None) -> Dict[str, Any]:
        """
        Immediately cancel a clinic's subscription.
        Locks portal access and marks any pending requests as cancelled.
        """
        business = db.session.get(Business, business_id)
        if not business:
            return {"success": False, "error": f"Clinic #{business_id} not found."}

        business.subscription_status = "cancelled"

        # Cancel any pending requests
        pending_requests = SubscriptionRequest.query.filter_by(
            business_id=business_id, status="pending"
        ).all()
        if pending_requests:
            for pr in pending_requests:
                pr.status = "cancelled"
                pr.reviewed_at = datetime.now(timezone.utc)
                pr.notes = reason or "Cancelled due to subscription cancellation"
        else:
            cancel_req = SubscriptionRequest(
                business_id=business_id,
                plan_name=business.active_plan_name or "Standard Plan",
                plan_display_name=business.active_plan_name or "Standard Plan",
                duration_days=0,
                requested_by_user="Platform Admin",
                status="cancelled",
                reviewed_at=datetime.now(timezone.utc),
                reviewed_by="Platform Admin",
                notes=reason or "Suspended by platform administration"
            )
            db.session.add(cancel_req)

        db.session.commit()
        return {
            "success": True,
            "message": f"Subscription for '{business.name}' has been cancelled.",
            "status": "cancelled",
            "is_valid": False
        }

    @staticmethod
    def reactivate_subscription(business_id: int, days: int = 30) -> Dict[str, Any]:
        """Reactivate a cancelled or expired clinic subscription."""
        business = db.session.get(Business, business_id)
        if not business:
            return {"success": False, "error": f"Clinic #{business_id} not found."}

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        business.subscription_status = "active"
        business.active_plan_name = business.active_plan_name or "Standard Monthly Plan (30 Days)"
        business.subscription_start_date = business.subscription_start_date or now_utc
        business.subscription_expires_at = now_utc + timedelta(days=days)

        # Wipe out any previous rejected or cancelled subscription notices
        prior_notices = SubscriptionRequest.query.filter(
            SubscriptionRequest.business_id == business.id,
            SubscriptionRequest.status.in_(["rejected", "cancelled"])
        ).all()
        for pn in prior_notices:
            pn.status = "archived"

        db.session.commit()

        return {
            "success": True,
            "message": f"Subscription for '{business.name}' reactivated with {days} days.",
            "status": "active",
            "subscription_expires_at": business.subscription_expires_at.strftime("%Y-%m-%d"),
            "days_remaining": business.days_remaining
        }

    @staticmethod
    def set_date_range(business_id: int, start_date: datetime, end_date: datetime) -> Dict[str, Any]:
        """Set explicit calendar start and end dates for a clinic's subscription."""
        business = db.session.get(Business, business_id)
        if not business:
            return {"success": False, "error": f"Clinic #{business_id} not found."}

        if end_date < start_date:
            return {"success": False, "error": "End date must be on or after start date."}

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        end_naive = end_date.replace(tzinfo=None) if end_date.tzinfo else end_date

        business.subscription_start_date = start_date
        business.subscription_expires_at = end_date
        business.subscription_status = "active" if end_naive >= now_utc else "expired"
        business.active_plan_name = f"Custom Schedule ({start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')})"

        if business.subscription_status == "active":
            prior_notices = SubscriptionRequest.query.filter(
                SubscriptionRequest.business_id == business.id,
                SubscriptionRequest.status.in_(["rejected", "cancelled"])
            ).all()
            for pn in prior_notices:
                pn.status = "archived"

        db.session.commit()
        return {
            "success": True,
            "message": f"Subscription dates updated for '{business.name}'.",
            "start_date": business.subscription_start_date.strftime("%Y-%m-%d"),
            "end_date": business.subscription_expires_at.strftime("%Y-%m-%d"),
            "status": business.subscription_status,
            "days_remaining": business.days_remaining
        }

    @staticmethod
    def extend_subscription(business_id: int, days: int = 30) -> Dict[str, Any]:
        """Quick direct extension by platform owner."""
        business = db.session.get(Business, business_id)
        if not business:
            return {"success": False, "error": f"Clinic with ID {business_id} not found."}

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

        if business.subscription_status == "active" and business.subscription_expires_at:
            exp = business.subscription_expires_at.replace(tzinfo=None) if business.subscription_expires_at.tzinfo else business.subscription_expires_at
            base_date = max(now_utc, exp)
        else:
            base_date = now_utc

        business.subscription_status = "active"
        business.subscription_start_date = business.subscription_start_date or now_utc
        business.subscription_expires_at = base_date + timedelta(days=days)

        prior_notices = SubscriptionRequest.query.filter(
            SubscriptionRequest.business_id == business.id,
            SubscriptionRequest.status.in_(["rejected", "cancelled"])
        ).all()
        for pn in prior_notices:
            pn.status = "archived"

        db.session.commit()

        return {
            "success": True,
            "business_id": business.id,
            "status": "active",
            "subscription_expires_at": business.subscription_expires_at.strftime("%Y-%m-%d"),
            "days_remaining": business.days_remaining
        }

    @staticmethod
    def get_pending_requests() -> List[Dict[str, Any]]:
        """Retrieve all subscription requests currently awaiting platform review."""
        requests = SubscriptionRequest.query.filter_by(
            status="pending"
        ).order_by(SubscriptionRequest.created_at.desc()).all()
        return [r.to_dict() for r in requests]
