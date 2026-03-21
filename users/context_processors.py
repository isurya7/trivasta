def user_agency(request):
    if not request.user.is_authenticated:
        return {'user_agency': None}
    try:
        agency = request.user.agency
        if agency.status == 'approved':
            return {'user_agency': agency}
    except Exception:
        pass
    return {'user_agency': None}